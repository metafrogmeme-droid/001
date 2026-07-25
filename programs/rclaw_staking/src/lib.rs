//! $RCLAW staking — DRAFT / DEVNET-ONLY Anchor program.
//!
//! ⚠️ **DO NOT DEPLOY TO ANY CLUSTER HOLDING REAL VALUE.**
//! This program is **unaudited**, and an earlier revision shipped a **critical
//! vault-drain vulnerability**: `StakeAccount` did not record which mint was
//! staked, so a stake denominated in a worthless self-minted token could be
//! redeemed against the real $RCLAW vault. That specific hole is fixed below
//! (stake records are bound to a mint, the vault authority is mint-scoped, and
//! the mint may be pinned), but the program has still had **no third-party
//! audit**. A real deployment stays gated behind the roadmap's Phase 0
//! Guardrails (legal review + smart-contract audit) — see docs/TOKEN_ROADMAP.md.
//!
//! A minimal non-custodial stake vault: users escrow tokens into a program-owned
//! vault and a per-user, per-mint `StakeAccount` PDA tracks the staked amount.
//! RUNECLAW's tier gate (`bot/token/tier_gate.py`) reads that staked amount to
//! unlock premium features. Users can unstake at any time.
//!
//! Token-2022 aware: uses `token_interface` + `transfer_checked`, so the real
//! $RCLAW mint (an SPL **Token-2022** mint — see `token/scripts/create_token.mjs`)
//! can actually be staked. The legacy SPL Token program also satisfies the
//! interface, so both work.
//!
//! `StakeAccount` layout (Anchor): 8-byte discriminator, then
//!   owner: Pubkey (32) @ offset  8
//!   mint:  Pubkey (32) @ offset 40
//!   amount: u64    (8) @ offset 72
//!   staked_at: i64 (8) @ offset 80
//!   bump: u8       (1) @ offset 88
//! The Python gate finds accounts via getProgramAccounts + a memcmp on `owner`
//! at offset 8 (and optionally `mint` at offset 40) and reads `amount` at
//! offset 72 — no PDA derivation needed. Keep these offsets in sync with
//! `bot/token/tier_gate.py`.
use anchor_lang::prelude::*;
use anchor_spl::{
    associated_token::AssociatedToken,
    token_interface::{
        self, Mint, TokenAccount, TokenInterface, TransferChecked,
    },
};

declare_id!("Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS");

/// Compile-time pin for the canonical $RCLAW mint, set from the environment at
/// build time:
///
/// ```bash
/// RCLAW_PINNED_MINT=<base58 mint address> anchor build
/// ```
///
/// It is deliberately **not** a hardcoded literal: the $RCLAW mint does not
/// exist yet (`token/` has never been run against a live cluster), and baking a
/// placeholder into a security constant would either brick staking or give false
/// assurance. `option_env!` makes the pin a real, enforced deployment setting
/// while keeping the source honest about what is not yet known.
///
/// **Unset (`None`)** — the program accepts any mint. Each mint still gets its
/// own isolated vault and stake records, so cross-mint drain is impossible, but
/// a user can stake a worthless token; the off-chain gate must then filter on
/// mint (`bot/token/tier_gate.py` does, via `RCLAW_MINT`).
///
/// **Set** — `stake` rejects every other mint with `UnexpectedMint`. Set it for
/// any deployment where tiers carry value.
pub const PINNED_MINT: Option<&str> = option_env!("RCLAW_PINNED_MINT");

/// Parse + compare the pin. Kept separate so the logic is unit-testable without
/// rebuilding under a different environment.
fn enforce_pinned_mint(pinned: Option<&str>, mint: &Pubkey) -> Result<()> {
    let Some(expected) = pinned else { return Ok(()) };
    let expected = expected.trim();
    if expected.is_empty() {
        return Ok(()); // treat an empty pin as unset
    }
    let expected: Pubkey = expected
        .parse()
        .map_err(|_| error!(StakeError::InvalidPinnedMint))?;
    require_keys_eq!(*mint, expected, StakeError::UnexpectedMint);
    Ok(())
}

fn check_pinned_mint(mint: &Pubkey) -> Result<()> {
    enforce_pinned_mint(PINNED_MINT, mint)
}

#[program]
pub mod rclaw_staking {
    use super::*;

    /// Stake `amount` base units. Creates the caller's per-mint `StakeAccount`
    /// PDA on first call and escrows tokens in that mint's vault.
    pub fn stake(ctx: Context<Stake>, amount: u64) -> Result<()> {
        require!(amount > 0, StakeError::ZeroAmount);
        check_pinned_mint(&ctx.accounts.mint.key())?;

        let sa = &mut ctx.accounts.stake_account;
        // On re-stake, the record must already belong to this owner+mint.
        // (`init_if_needed` leaves prior state intact, so assert rather than
        // blindly overwrite — a mismatch means the PDA was crafted or reused.)
        if sa.amount > 0 {
            require_keys_eq!(sa.owner, ctx.accounts.owner.key(), StakeError::WrongOwner);
            require_keys_eq!(sa.mint, ctx.accounts.mint.key(), StakeError::WrongMint);
        }

        token_interface::transfer_checked(
            CpiContext::new(
                ctx.accounts.token_program.to_account_info(),
                TransferChecked {
                    from: ctx.accounts.user_token_account.to_account_info(),
                    mint: ctx.accounts.mint.to_account_info(),
                    to: ctx.accounts.vault.to_account_info(),
                    authority: ctx.accounts.owner.to_account_info(),
                },
            ),
            amount,
            ctx.accounts.mint.decimals,
        )?;

        let sa = &mut ctx.accounts.stake_account;
        sa.owner = ctx.accounts.owner.key();
        sa.mint = ctx.accounts.mint.key();
        sa.amount = sa.amount.checked_add(amount).ok_or(StakeError::Overflow)?;
        sa.staked_at = Clock::get()?.unix_timestamp;
        sa.bump = ctx.bumps.stake_account;
        emit!(Staked {
            owner: sa.owner,
            mint: sa.mint,
            amount,
            total: sa.amount,
        });
        Ok(())
    }

    /// Unstake `amount` base units back to the caller's token account.
    ///
    /// The stake record is bound to `mint` (`has_one = mint`) and the vault
    /// authority is derived per-mint, so a stake of one token can never be
    /// redeemed out of another token's vault.
    pub fn unstake(ctx: Context<Unstake>, amount: u64) -> Result<()> {
        let staked = ctx.accounts.stake_account.amount;
        require!(amount > 0 && amount <= staked, StakeError::InsufficientStake);

        let mint_key = ctx.accounts.mint.key();
        let vault_bump = ctx.bumps.vault_authority;
        let seeds: &[&[u8]] = &[b"vault", mint_key.as_ref(), &[vault_bump]];
        let signer = &[seeds];

        token_interface::transfer_checked(
            CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                TransferChecked {
                    from: ctx.accounts.vault.to_account_info(),
                    mint: ctx.accounts.mint.to_account_info(),
                    to: ctx.accounts.user_token_account.to_account_info(),
                    authority: ctx.accounts.vault_authority.to_account_info(),
                },
                signer,
            ),
            amount,
            ctx.accounts.mint.decimals,
        )?;

        let sa = &mut ctx.accounts.stake_account;
        sa.amount = sa.amount.checked_sub(amount).ok_or(StakeError::Overflow)?;
        emit!(Unstaked {
            owner: sa.owner,
            mint: sa.mint,
            amount,
            total: sa.amount,
        });
        Ok(())
    }
}

#[derive(Accounts)]
pub struct Stake<'info> {
    #[account(mut)]
    pub owner: Signer<'info>,

    /// The mint being staked. Each mint is fully isolated (own vault, own
    /// stake records). Pin it via `PINNED_MINT` for a value-bearing deployment.
    pub mint: InterfaceAccount<'info, Mint>,

    /// Per-user, PER-MINT stake record. Seeds: ["stake", owner, mint].
    #[account(
        init_if_needed,
        payer = owner,
        space = 8 + StakeAccount::SPACE,
        seeds = [b"stake", owner.key().as_ref(), mint.key().as_ref()],
        bump,
    )]
    pub stake_account: Account<'info, StakeAccount>,

    /// Mint-scoped vault authority PDA. Seeds: ["vault", mint].
    /// CHECK: PDA used only as the vault token account's authority.
    #[account(seeds = [b"vault", mint.key().as_ref()], bump)]
    pub vault_authority: UncheckedAccount<'info>,

    /// The vault token account (ATA of this mint's vault authority).
    #[account(
        init_if_needed,
        payer = owner,
        associated_token::mint = mint,
        associated_token::authority = vault_authority,
        associated_token::token_program = token_program,
    )]
    pub vault: InterfaceAccount<'info, TokenAccount>,

    #[account(
        mut,
        constraint = user_token_account.owner == owner.key() @ StakeError::WrongOwner,
        constraint = user_token_account.mint == mint.key() @ StakeError::WrongMint,
    )]
    pub user_token_account: InterfaceAccount<'info, TokenAccount>,

    pub token_program: Interface<'info, TokenInterface>,
    pub associated_token_program: Program<'info, AssociatedToken>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct Unstake<'info> {
    #[account(mut)]
    pub owner: Signer<'info>,

    pub mint: InterfaceAccount<'info, Mint>,

    #[account(
        mut,
        seeds = [b"stake", owner.key().as_ref(), mint.key().as_ref()],
        bump = stake_account.bump,
        has_one = owner @ StakeError::WrongOwner,
        has_one = mint @ StakeError::WrongMint,
    )]
    pub stake_account: Account<'info, StakeAccount>,

    /// CHECK: mint-scoped PDA authority for the vault token account.
    #[account(seeds = [b"vault", mint.key().as_ref()], bump)]
    pub vault_authority: UncheckedAccount<'info>,

    #[account(
        mut,
        associated_token::mint = mint,
        associated_token::authority = vault_authority,
        associated_token::token_program = token_program,
    )]
    pub vault: InterfaceAccount<'info, TokenAccount>,

    #[account(
        mut,
        constraint = user_token_account.owner == owner.key() @ StakeError::WrongOwner,
        constraint = user_token_account.mint == mint.key() @ StakeError::WrongMint,
    )]
    pub user_token_account: InterfaceAccount<'info, TokenAccount>,

    pub token_program: Interface<'info, TokenInterface>,
}

#[account]
pub struct StakeAccount {
    pub owner: Pubkey,
    pub mint: Pubkey,
    pub amount: u64,
    pub staked_at: i64,
    pub bump: u8,
}

impl StakeAccount {
    // owner(32) + mint(32) + amount(8) + staked_at(8) + bump(1)
    pub const SPACE: usize = 32 + 32 + 8 + 8 + 1;
}

#[event]
pub struct Staked {
    pub owner: Pubkey,
    pub mint: Pubkey,
    pub amount: u64,
    pub total: u64,
}

#[event]
pub struct Unstaked {
    pub owner: Pubkey,
    pub mint: Pubkey,
    pub amount: u64,
    pub total: u64,
}

#[error_code]
pub enum StakeError {
    #[msg("Amount must be greater than zero")]
    ZeroAmount,
    #[msg("Insufficient staked balance")]
    InsufficientStake,
    #[msg("Arithmetic overflow")]
    Overflow,
    #[msg("Token account owner mismatch")]
    WrongOwner,
    #[msg("Token account mint mismatch")]
    WrongMint,
    #[msg("Mint does not match the pinned $RCLAW mint")]
    UnexpectedMint,
    #[msg("RCLAW_PINNED_MINT was set to something that is not a valid base58 pubkey")]
    InvalidPinnedMint,
}

#[cfg(test)]
mod pin_tests {
    use super::*;
    use std::str::FromStr;

    const A: &str = "So11111111111111111111111111111111111111112";
    const B: &str = "GNS1S5J5AspKXgpjz6SvKL66kPaKWAhaGRhCqPRxii2B";

    #[test]
    fn unset_pin_accepts_any_mint() {
        let any = Pubkey::from_str(A).unwrap();
        assert!(enforce_pinned_mint(None, &any).is_ok());
        assert!(enforce_pinned_mint(Some("   "), &any).is_ok(), "empty pin == unset");
    }

    #[test]
    fn set_pin_accepts_only_that_mint() {
        let a = Pubkey::from_str(A).unwrap();
        let b = Pubkey::from_str(B).unwrap();
        assert!(enforce_pinned_mint(Some(A), &a).is_ok());
        assert!(enforce_pinned_mint(Some(A), &b).is_err(), "a different mint must be rejected");
        // Surrounding whitespace from an env var must not break the pin.
        assert!(enforce_pinned_mint(Some(" \nSo11111111111111111111111111111111111111112 "), &a).is_ok());
    }

    #[test]
    fn malformed_pin_is_rejected_not_ignored() {
        let a = Pubkey::from_str(A).unwrap();
        assert!(
            enforce_pinned_mint(Some("not-a-pubkey"), &a).is_err(),
            "a typo'd pin must fail closed, never silently accept every mint"
        );
    }
}
