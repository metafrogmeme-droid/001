// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {DrainHash} from "../src/DrainHash.sol";
import {FirewallAccount} from "../src/FirewallAccount.sol";

contract Tok {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        require(balanceOf[msg.sender] >= amount, "bal");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function setApprovalForAll(address, bool) external pure returns (bool) {
        return true;
    }

    receive() external payable {}

    fallback() external payable {}
}

contract RevertTok {
    function balanceOf(address) external pure returns (uint256) {
        return 5;
    }

    function transfer(address, uint256) external pure returns (bool) {
        revert("no");
    }
}

contract ReenterTok {
    address public acct;
    uint256 public calls;

    function set(address a) external {
        acct = a;
    }

    function transfer(address, uint256) external returns (bool) {
        calls += 1;
        FirewallAccount(payable(acct)).execute(acct, 0, "");
        return true;
    }
}

/// @dev OZ-style permit: ecrecover only. It never asks the owner for ERC-1271.
contract EcrecoverOnlyPermit {
    uint256 public asked1271;
    address public lastSigner;

    function permit(address owner, address spender, uint256 value, uint256 nonce, uint256 deadline, uint8 v, bytes32 r, bytes32 s)
        external
    {
        bytes32 hash = keccak256(abi.encode(owner, spender, value, nonce, deadline));
        lastSigner = ecrecover(hash, v, r, s);
        require(lastSigner == owner, "signer");
        spender;
        value;
    }

    function isValidSignature(bytes32, bytes calldata) external returns (bytes4) {
        asked1271 += 1;
        return 0x1626ba7e;
    }
}

contract DomTok {
    function DOMAIN_SEPARATOR() external pure returns (bytes32) {
        return bytes32(uint256(0xD0));
    }

    function nonces(address) external pure returns (uint256) {
        return 7;
    }
}

/// @dev Independent Permit2 stand-in. The hash is written out here, not delegated
///      to DrainHash, so a field-order mistake fails the equality test.
contract Permit2Mock {
    uint256 public path;
    bytes32 public lastDigest;

    function hashSingle(DrainHash.PermitSingle memory p) public view returns (bytes32) {
        bytes32 detailsType = keccak256("PermitDetails(address token,uint160 amount,uint48 expiration,uint48 nonce)");
        bytes32 singleType = keccak256(
            "PermitSingle(PermitDetails details,address spender,uint256 sigDeadline)PermitDetails(address token,uint160 amount,uint48 expiration,uint48 nonce)"
        );
        bytes32 domainType = keccak256("EIP712Domain(string name,uint256 chainId,address verifyingContract)");
        bytes32 detailsHash = keccak256(abi.encode(detailsType, p.details.token, p.details.amount, p.details.expiration, p.details.nonce));
        bytes32 structHash = keccak256(abi.encode(singleType, detailsHash, p.spender, p.sigDeadline));
        bytes32 domain = keccak256(abi.encode(domainType, keccak256("Permit2"), block.chainid, address(this)));
        return keccak256(abi.encodePacked(hex"1901", domain, structHash));
    }

    function permit(address owner, DrainHash.PermitSingle calldata permitSingle, bytes calldata sig) external {
        bytes32 digest = hashSingle(permitSingle);
        lastDigest = digest;
        if (owner.code.length > 0) {
            (bool ok, bytes memory ret) = owner.staticcall(abi.encodeWithSelector(bytes4(0x1626ba7e), digest, sig));
            require(ok && ret.length >= 32 && bytes4(ret) == bytes4(0x1626ba7e), "1271");
            path = 2;
            return;
        }
        require(sig.length == 65, "sig");
        bytes32 r;
        bytes32 s;
        uint8 v;
        assembly {
            r := calldataload(sig.offset)
            s := calldataload(add(sig.offset, 32))
            v := byte(0, calldataload(add(sig.offset, 64)))
        }
        require(ecrecover(digest, v, r, s) == owner, "signer");
        path = 1;
    }
}
