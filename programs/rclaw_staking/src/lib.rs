//! $RCLAW staking — DRAFT / DEVNET-ONLY Anchor program.
//!
//! A minimal non-custodial stake vault: users escrow $RCLAW into a program-owned
//! vault and a per-user `StakeAccount` PDA tracks the staked amount. RUNECLAW's
//! tier gate (`bot/token/tier_gate.py`) reads that staked amount to unlock
//! premium features. Users can unstake at any time (optional cooldown left as a
//! future extension). Nothing here is audited or intended for mainnet — a real
//! launch is gated behind the roadmap's Phase 0 Guardrails (legal + audit).
//!
//! `StakeAccount` layout (Anchor): 8-byte discriminator, then
//!   owner: Pubkey (32) @ offset 8
//!   amount: u64      (8) @ offset 40
//!   staked_at: i64   (8) @ offset 48
//!   bump: u8         (1) @ offset 56
//! The Python gate finds accounts via getProgramAccounts + a memcmp on `owner`
//! at offset 8 and reads `amount` at offset 40 — no PDA derivation needed.
use anchor_lang::prelude::*;
use anchor_spl::{
    associated_token::AssociatedToken,
    token::{self, Mint, Token, TokenAccount, Transfer},
};

declare_id!("Fg6PaFpoGXkYsidMpWTK6W2BeZ7FEfcYkg476zPFsLnS");

#[program]
pub mod rclaw_staking {
    use super::*;

    /// Stake `amount` base units of $RCLAW. Creates the caller's `StakeAccount`
    /// PDA on first call (via `init_if_needed`) and escrows tokens in the vault.
    pub fn stake(ctx: Context<Stake>, amount: u64) -> Result<()> {
        require!(amount > 0, StakeError::ZeroAmount);

        token::transfer(
            CpiContext::new(
                ctx.accounts.token_program.to_account_info(),
                Transfer {
                    from: ctx.accounts.user_token_account.to_account_info(),
                    to: ctx.accounts.vault.to_account_info(),
                    authority: ctx.accounts.owner.to_account_info(),
                },
            ),
            amount,
        )?;

        let sa = &mut ctx.accounts.stake_account;
        sa.owner = ctx.accounts.owner.key();
        sa.amount = sa.amount.checked_add(amount).ok_or(StakeError::Overflow)?;
        sa.staked_at = Clock::get()?.unix_timestamp;
        sa.bump = ctx.bumps.stake_account;
        emit!(Staked { owner: sa.owner, amount, total: sa.amount });
        Ok(())
    }

    /// Unstake `amount` base units back to the caller's token account.
    pub fn unstake(ctx: Context<Unstake>, amount: u64) -> Result<()> {
        let staked = ctx.accounts.stake_account.amount;
        require!(amount > 0 && amount <= staked, StakeError::InsufficientStake);

        let vault_bump = ctx.bumps.vault_authority;
        let seeds: &[&[u8]] = &[b"vault", &[vault_bump]];
        let signer = &[seeds];

        token::transfer(
            CpiContext::new_with_signer(
                ctx.accounts.token_program.to_account_info(),
                Transfer {
                    from: ctx.accounts.vault.to_account_info(),
                    to: ctx.accounts.user_token_account.to_account_info(),
                    authority: ctx.accounts.vault_authority.to_account_info(),
                },
                signer,
            ),
            amount,
        )?;

        let sa = &mut ctx.accounts.stake_account;
        sa.amount = sa.amount.checked_sub(amount).ok_or(StakeError::Overflow)?;
        emit!(Unstaked { owner: sa.owner, amount, total: sa.amount });
        Ok(())
    }
}

#[derive(Accounts)]
pub struct Stake<'info> {
    #[account(mut)]
    pub owner: Signer<'info>,

    /// The $RCLAW mint being staked.
    pub mint: Account<'info, Mint>,

    /// Per-user stake record. Seeds: ["stake", owner].
    #[account(
        init_if_needed,
        payer = owner,
        space = 8 + StakeAccount::SPACE,
        seeds = [b"stake", owner.key().as_ref()],
        bump,
    )]
    pub stake_account: Account<'info, StakeAccount>,

    /// Program vault authority PDA. Seeds: ["vault"].
    /// CHECK: PDA used only as the vault's token-account authority.
    #[account(seeds = [b"vault"], bump)]
    pub vault_authority: UncheckedAccount<'info>,

    /// The vault token account (ATA of the vault authority for `mint`).
    #[account(
        init_if_needed,
        payer = owner,
        associated_token::mint = mint,
        associated_token::authority = vault_authority,
    )]
    pub vault: Account<'info, TokenAccount>,

    #[account(
        mut,
        constraint = user_token_account.owner == owner.key() @ StakeError::WrongOwner,
        constraint = user_token_account.mint == mint.key() @ StakeError::WrongMint,
    )]
    pub user_token_account: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
    pub associated_token_program: Program<'info, AssociatedToken>,
    pub system_program: Program<'info, System>,
}

#[derive(Accounts)]
pub struct Unstake<'info> {
    #[account(mut)]
    pub owner: Signer<'info>,

    pub mint: Account<'info, Mint>,

    #[account(
        mut,
        seeds = [b"stake", owner.key().as_ref()],
        bump = stake_account.bump,
        has_one = owner @ StakeError::WrongOwner,
    )]
    pub stake_account: Account<'info, StakeAccount>,

    /// CHECK: PDA authority for the vault token account.
    #[account(seeds = [b"vault"], bump)]
    pub vault_authority: UncheckedAccount<'info>,

    #[account(
        mut,
        associated_token::mint = mint,
        associated_token::authority = vault_authority,
    )]
    pub vault: Account<'info, TokenAccount>,

    #[account(
        mut,
        constraint = user_token_account.owner == owner.key() @ StakeError::WrongOwner,
        constraint = user_token_account.mint == mint.key() @ StakeError::WrongMint,
    )]
    pub user_token_account: Account<'info, TokenAccount>,

    pub token_program: Program<'info, Token>,
}

#[account]
pub struct StakeAccount {
    pub owner: Pubkey,
    pub amount: u64,
    pub staked_at: i64,
    pub bump: u8,
}

impl StakeAccount {
    // owner(32) + amount(8) + staked_at(8) + bump(1)
    pub const SPACE: usize = 32 + 8 + 8 + 1;
}

#[event]
pub struct Staked {
    pub owner: Pubkey,
    pub amount: u64,
    pub total: u64,
}

#[event]
pub struct Unstaked {
    pub owner: Pubkey,
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
}
