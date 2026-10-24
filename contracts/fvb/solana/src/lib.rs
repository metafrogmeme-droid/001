//! Devnet consumer for the fair-value bridge.
//!
//! Host-tested. This crate is its own cargo workspace, so `cargo test`
//! here does not walk up into the staking workspace or rewrite that
//! lockfile. No SBF artifact is built: ed25519-dalek is not cross-compiled
//! in this repository.
//!
//! A packet is the same nine words the Python signer hashes for ed25519
//! (`keccak256` of the field encoding, not the EIP-712 digest). Three of
//! five committee keys must sign it. Any cluster other than `devnet` is
//! refused at init and again at submit.

use ed25519_dalek::{Signature, Verifier, VerifyingKey};
use tiny_keccak::{Hasher, Keccak};

/// Not the staking program id `6yGc2n7vZyp7nvJJ8uXEdy56P1UT8Ma4En26bTtBrJhW`,
/// and not the pinned staking mint.
pub const PROGRAM_ID: [u8; 32] = [
    0xf7, 0xb0, 0xc0, 0xde, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11,
    0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11,
];

pub const WORD: usize = 32;
pub const WORDS: usize = 9;

#[derive(Clone, Debug)]
pub struct Committee {
    cluster: String,
    signers: Vec<[u8; 32]>,
    threshold: u8,
}

pub fn init(cluster: &str, signers: &[[u8; 32]], threshold: u8) -> Result<Committee, &'static str> {
    if cluster != "devnet" {
        return Err("devnet only");
    }
    committee_from(cluster, signers, threshold)
}

fn committee_from(cluster: &str, signers: &[[u8; 32]], threshold: u8) -> Result<Committee, &'static str> {
    if threshold != 3 || signers.len() != 5 {
        return Err("3-of-5");
    }
    let mut seen = Vec::with_capacity(5);
    for key in signers {
        if key.iter().all(|b| *b == 0) || seen.contains(key) {
            return Err("signer");
        }
        seen.push(*key);
    }
    Ok(Committee {
        cluster: cluster.to_string(),
        signers: seen,
        threshold,
    })
}

pub fn message_of(words: &[u8]) -> Result<[u8; 32], &'static str> {
    if words.len() != WORD * WORDS {
        return Err("words");
    }
    Ok(keccak256(words))
}

pub fn submit(
    committee: &Committee,
    words: &[u8],
    sigs: &[([u8; 32], [u8; 64])],
) -> Result<(), &'static str> {
    if committee.cluster != "devnet" {
        return Err("devnet only");
    }
    if words.len() != WORD * WORDS {
        return Err("words");
    }
    if word_is_zero(words, 1) || word_is_zero(words, 2) || word_is_zero(words, 3) || word_is_zero(words, 6) {
        return Err("unreadable");
    }
    if !word_eq(words, 5, 1) {
        return Err("unconfirmed");
    }
    if !word_eq(words, 7, 1) {
        return Err("version");
    }
    let message = keccak256(words);
    let mut accepted: Vec<[u8; 32]> = Vec::new();
    for (pk, sig) in sigs {
        if !committee.signers.iter().any(|s| s == pk) || accepted.iter().any(|s| s == pk) {
            continue;
        }
        if verify(pk, &message, sig) {
            accepted.push(*pk);
        }
    }
    if accepted.len() < usize::from(committee.threshold) {
        Err("threshold")
    } else {
        Ok(())
    }
}

fn keccak256(data: &[u8]) -> [u8; 32] {
    let mut hasher = Keccak::v256();
    hasher.update(data);
    let mut out = [0u8; 32];
    hasher.finalize(&mut out);
    out
}

fn word_is_zero(words: &[u8], index: usize) -> bool {
    words[index * WORD..(index + 1) * WORD].iter().all(|b| *b == 0)
}

fn word_eq(words: &[u8], index: usize, value: u8) -> bool {
    let w = &words[index * WORD..(index + 1) * WORD];
    w[..WORD - 1].iter().all(|b| *b == 0) && w[WORD - 1] == value
}

fn verify(pk: &[u8; 32], message: &[u8], sig: &[u8; 64]) -> bool {
    let Ok(key) = VerifyingKey::from_bytes(pk) else {
        return false;
    };
    let Ok(signature) = Signature::from_slice(sig) else {
        return false;
    };
    key.verify(message, &signature).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use ed25519_dalek::{Signer, SigningKey};

    fn keys() -> Vec<SigningKey> {
        (1u8..=5).map(|i| SigningKey::from_bytes(&[i; 32])).collect()
    }

    fn pubs(keys: &[SigningKey]) -> Vec<[u8; 32]> {
        keys.iter().map(|k| k.verifying_key().to_bytes()).collect()
    }

    fn put(words: &mut [u8], index: usize, value: u64) {
        let start = index * WORD;
        words[start..start + WORD].fill(0);
        words[start + WORD - 8..start + WORD].copy_from_slice(&value.to_be_bytes());
    }

    fn sample() -> Vec<u8> {
        let mut words = vec![0u8; WORD * WORDS];
        words[..3].copy_from_slice(b"SPY");
        put(&mut words, 1, 100);
        put(&mut words, 2, 99);
        put(&mut words, 3, 101);
        put(&mut words, 5, 1);
        put(&mut words, 6, 1_700_000_000);
        put(&mut words, 7, 1);
        words
    }

    fn sigs(keys: &[SigningKey], message: &[u8]) -> Vec<([u8; 32], [u8; 64])> {
        keys.iter()
            .map(|k| (k.verifying_key().to_bytes(), k.sign(message).to_bytes()))
            .collect()
    }

    #[test]
    fn the_program_id_is_not_the_staking_program_or_the_pinned_mint() {
        // 6yGc2n7vZyp7nvJJ8uXEdy56P1UT8Ma4En26bTtBrJhW and the pinned mint.
        let staking = [
            0x58, 0xb4, 0x9a, 0x79, 0x42, 0x5a, 0xa2, 0xa4, 0x75, 0xf5, 0x04, 0xc1, 0xc6, 0xc1, 0x37, 0xfa,
            0x4e, 0x21, 0xb1, 0xd3, 0xa8, 0xa2, 0x6a, 0x41, 0xa8, 0x72, 0xac, 0x43, 0x91, 0xbf, 0x8e, 0x39,
        ];
        let mint = [
            0xc6, 0xfa, 0x7a, 0xf3, 0xbe, 0xdb, 0xad, 0x3a, 0x3d, 0x65, 0xf3, 0x6a, 0xab, 0xc9, 0x74, 0x31,
            0xb1, 0xbb, 0xe4, 0xc2, 0xd2, 0xf6, 0xe0, 0xe4, 0x7c, 0xa6, 0x02, 0x03, 0x45, 0x2f, 0x5d, 0x61,
        ];
        assert_ne!(PROGRAM_ID, staking);
        assert_ne!(PROGRAM_ID, mint);
        assert_eq!(keccak256(b""), hex("c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"));
    }

    #[test]
    fn only_devnet_initialises() {
        let pubs = pubs(&keys());
        assert!(init("devnet", &pubs, 3).is_ok());
        for cluster in ["mainnet", "mainnet-beta", "testnet", ""] {
            assert_eq!(init(cluster, &pubs, 3).unwrap_err(), "devnet only");
        }
        assert_eq!(init("devnet", &pubs[..4], 3).unwrap_err(), "3-of-5");
        let mut dup = pubs.clone();
        dup[4] = dup[0];
        assert_eq!(init("devnet", &dup, 3).unwrap_err(), "signer");
    }

    #[test]
    fn three_of_five_accepts_and_two_does_not() {
        let keys = keys();
        let committee = init("devnet", &pubs(&keys), 3).unwrap();
        let words = sample();
        let message = message_of(&words).unwrap();
        let all = sigs(&keys, &message);
        assert_eq!(submit(&committee, &words, &all[..2]), Err("threshold"));
        let tripled = vec![all[0], all[0], all[0]];
        assert_eq!(submit(&committee, &words, &tripled), Err("threshold"));
        assert_eq!(submit(&committee, &words, &all[..3]), Ok(()));

        let mut crashed = words.clone();
        put(&mut crashed, 1, 1);
        let stale = sigs(&keys[..3], &message);
        assert_eq!(submit(&committee, &crashed, &stale), Err("threshold"));

        let mut quiet = words.clone();
        put(&mut quiet, 5, 0);
        let no = sigs(&keys[..3], &message_of(&quiet).unwrap());
        assert_eq!(submit(&committee, &quiet, &no), Err("unconfirmed"));

        let mut zero = words.clone();
        put(&mut zero, 1, 0);
        assert_eq!(submit(&committee, &zero, &all[..3]), Err("unreadable"));
    }

    #[test]
    fn submit_refuses_a_non_devnet_record_even_with_five_signatures() {
        let keys = keys();
        let mut committee = init("devnet", &pubs(&keys), 3).unwrap();
        committee.cluster = "mainnet-beta".to_string();
        let words = sample();
        let all = sigs(&keys, &message_of(&words).unwrap());
        assert_eq!(submit(&committee, &words, &all), Err("devnet only"));
    }

    fn hex(s: &str) -> [u8; 32] {
        let bytes = (0..32)
            .map(|i| u8::from_str_radix(&s[i * 2..i * 2 + 2], 16).unwrap())
            .collect::<Vec<_>>();
        bytes.try_into().unwrap()
    }
}
