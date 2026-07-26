// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.24;

/**
 * RUNECLAW Rune of Entry — a soulbound sigil, free forever, one per wallet.
 *
 * What this is: a membership badge minted by a user's OWN wallet (the server
 * never signs or sends transactions) after signup, gated by an EIP-712
 * voucher from an off-chain signer. The art is generated and stored fully
 * on-chain — no IPFS, no server, no link that can die.
 *
 * What this is NOT, by construction:
 * - Not an investment. mintPrice is not a variable that exists; no ether is
 *   ever payable into this contract; the metadata says "a badge, not an
 *   investment" on every token.
 * - Not transferable. Every transfer and approval path reverts ("soulbound"),
 *   and ERC-5192 locked() answers true so wallets and marketplaces can see
 *   the lock without trying.
 * - Not upgradeable, not pausable, not owner-controlled: there is no owner,
 *   no admin function, and the voucher signer is immutable. What is deployed
 *   is what runs, forever.
 *
 * Voucher-key blast radius: the off-chain voucher key gates WHO may mint. It
 * can never move funds or touch an existing token — worst-case compromise is
 * unauthorized free mints, nothing else.
 */
contract RuneOfEntry {
    string public constant name = "RUNECLAW Rune of Entry";
    string public constant symbol = "RUNE";

    address public immutable voucherSigner;
    uint256 public totalMinted;

    mapping(uint256 => address) private _owners;
    // wallet => tokenId (0 = has not minted). This single mapping IS the
    // one-per-wallet rule.
    mapping(address => uint256) public tokenOf;

    event Transfer(address indexed from, address indexed to, uint256 indexed tokenId);
    /// ERC-5192: emitted once at mint; the token never unlocks.
    event Locked(uint256 tokenId);

    bytes32 private constant DOMAIN_TYPEHASH =
        keccak256("EIP712Domain(string name,uint256 chainId,address verifyingContract)");
    bytes32 private constant VOUCHER_TYPEHASH = keccak256("MintVoucher(address to)");

    constructor(address signer) {
        require(signer != address(0), "signer required");
        voucherSigner = signer;
    }

    // ── mint ────────────────────────────────────────────────────────────────

    /// Free, gas only — deliberately not payable. The voucher binds to
    /// msg.sender, this chain and this contract, so it cannot be replayed
    /// for another wallet or on another deployment.
    function mint(bytes calldata sig) external returns (uint256 id) {
        require(tokenOf[msg.sender] == 0, "already minted");
        bytes32 digest = keccak256(abi.encodePacked(
            "\x19\x01",
            keccak256(abi.encode(
                DOMAIN_TYPEHASH, keccak256(bytes(name)), block.chainid, address(this))),
            keccak256(abi.encode(VOUCHER_TYPEHASH, msg.sender))
        ));
        require(_recover(digest, sig) == voucherSigner, "bad voucher");
        id = ++totalMinted;
        _owners[id] = msg.sender;
        tokenOf[msg.sender] = id;
        emit Transfer(address(0), msg.sender, id);
        emit Locked(id);
    }

    function _recover(bytes32 digest, bytes calldata sig) private pure returns (address) {
        if (sig.length != 65) return address(0);
        bytes32 r = bytes32(sig[0:32]);
        bytes32 s = bytes32(sig[32:64]);
        uint8 v = uint8(sig[64]);
        if (v < 27) v += 27;
        return ecrecover(digest, v, r, s);
    }

    // ── ERC-721 reads ───────────────────────────────────────────────────────

    function ownerOf(uint256 id) public view returns (address o) {
        o = _owners[id];
        require(o != address(0), "no such token");
    }

    function balanceOf(address a) external view returns (uint256) {
        require(a != address(0), "zero address");
        return tokenOf[a] == 0 ? 0 : 1;
    }

    // ── soulbound: every movement path reverts ──────────────────────────────

    function transferFrom(address, address, uint256) external pure { revert("soulbound"); }
    function safeTransferFrom(address, address, uint256) external pure { revert("soulbound"); }
    function safeTransferFrom(address, address, uint256, bytes calldata) external pure { revert("soulbound"); }
    function approve(address, uint256) external pure { revert("soulbound"); }
    function setApprovalForAll(address, bool) external pure { revert("soulbound"); }
    function getApproved(uint256) external pure returns (address) { return address(0); }
    function isApprovedForAll(address, address) external pure returns (bool) { return false; }

    /// ERC-5192: permanently locked.
    function locked(uint256 id) external view returns (bool) {
        ownerOf(id); // existence check, reverts on unknown token
        return true;
    }

    function supportsInterface(bytes4 iid) external pure returns (bool) {
        return iid == 0x01ffc9a7   // ERC-165
            || iid == 0x80ac58cd   // ERC-721
            || iid == 0x5b5e139f   // ERC-721 Metadata
            || iid == 0xb45a3c0e;  // ERC-5192 (soulbound)
    }

    // ── fully on-chain art ──────────────────────────────────────────────────
    //
    // The rune is drawn from keccak(id, owner): a central staff plus up to
    // twelve branch strokes, each present iff its seed bit is set, two
    // guaranteed so no token is ever an empty staff. Deterministic forever:
    // the same token always renders the same sigil, with no dependency on
    // any server, gateway or file host.

    // 12 candidate branches as (x1,y1,x2,y2) bytes, coordinates 0-255,
    // drawn on a 350x350 canvas with a +47 offset applied at render time.
    bytes private constant BRANCHES =
        hex"80503CA0"  // upper-left  descend
        hex"8050C4A0"  // upper-right descend
        hex"803C5014"  // crown left
        hex"803CB014"  // crown right
        hex"80783C50"  // mid-left ascend
        hex"8078C450"  // mid-right ascend
        hex"80A03CC8"  // lower-left descend
        hex"80A0C4C8"  // lower-right descend
        hex"80C850F0"  // foot left
        hex"80C8B0F0"  // foot right
        hex"3C6EC46E"  // crossbar high
        hex"3CA0C4A0"; // crossbar low

    function seedOf(uint256 id) public view returns (bytes32) {
        return keccak256(abi.encodePacked(id, ownerOf(id)));
    }

    function _svg(uint256 id) private view returns (bytes memory out) {
        bytes32 seed = seedOf(id);
        out = abi.encodePacked(
            "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 350 350'>",
            "<rect width='350' height='350' fill='#0a0b10'/>",
            "<g stroke='#e6c34b' stroke-width='7' stroke-linecap='round' fill='none'>",
            "<line x1='175' y1='40' x2='175' y2='310'/>");
        uint256 drawn = 0;
        for (uint256 i = 0; i < 12; i++) {
            // Bit i picks branch i; the last two slots force the guarantee.
            bool pick = (uint256(seed) >> i) & 1 == 1;
            if (!pick && drawn == 0 && i == 10) pick = true;
            if (!pick && drawn == 1 && i == 11) pick = true;
            if (!pick) continue;
            drawn++;
            // Widen BEFORE offsetting: byte + 47 can exceed 255 (foot rows sit
            // at 0xF0), and uint8 arithmetic would revert the whole tokenURI.
            out = abi.encodePacked(out, "<line x1='", _u(uint256(uint8(BRANCHES[i * 4])) + 47),
                "' y1='", _u(uint256(uint8(BRANCHES[i * 4 + 1])) + 47),
                "' x2='", _u(uint256(uint8(BRANCHES[i * 4 + 2])) + 47),
                "' y2='", _u(uint256(uint8(BRANCHES[i * 4 + 3])) + 47), "'/>");
        }
        out = abi.encodePacked(out, "</g></svg>");
    }

    function tokenURI(uint256 id) external view returns (string memory) {
        ownerOf(id); // reverts for unknown tokens
        bytes memory json = abi.encodePacked(
            '{"name":"Rune of Entry #', _u(id),
            '","description":"A soulbound sigil marking this wallet\'s entry into RUNECLAW. ',
            'Minted free (gas only); art generated and stored fully on-chain. ',
            'A badge, not an investment: it promises nothing, costs nothing and cannot be transferred.",',
            '"image":"data:image/svg+xml;base64,', _b64(_svg(id)), '"}');
        return string(abi.encodePacked("data:application/json;base64,", _b64(json)));
    }

    // ── tiny pure helpers ───────────────────────────────────────────────────

    function _u(uint256 v) private pure returns (bytes memory) {
        if (v == 0) return "0";
        uint256 t = v; uint256 len;
        while (t != 0) { len++; t /= 10; }
        bytes memory b = new bytes(len);
        while (v != 0) { len--; b[len] = bytes1(uint8(48 + v % 10)); v /= 10; }
        return b;
    }

    bytes private constant B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";

    function _b64(bytes memory data) private pure returns (bytes memory result) {
        if (data.length == 0) return "";
        result = new bytes(4 * ((data.length + 2) / 3));
        uint256 j = 0;
        for (uint256 i = 0; i < data.length; i += 3) {
            uint256 a = uint8(data[i]);
            uint256 b = i + 1 < data.length ? uint8(data[i + 1]) : 0;
            uint256 c = i + 2 < data.length ? uint8(data[i + 2]) : 0;
            uint256 triple = (a << 16) | (b << 8) | c;
            result[j++] = B64[(triple >> 18) & 63];
            result[j++] = B64[(triple >> 12) & 63];
            result[j++] = i + 1 < data.length ? B64[(triple >> 6) & 63] : bytes1("=");
            result[j++] = i + 2 < data.length ? B64[triple & 63] : bytes1("=");
        }
    }
}
