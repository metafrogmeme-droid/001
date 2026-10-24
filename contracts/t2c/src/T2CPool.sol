// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.24;

/// A pool users fund from their own wallet. Nothing in this repository
/// deploys it. It starts paused. The cap is immutable. Withdrawal NFTs
/// move when the chain id is a fork or a named testnet.
contract T2CPool {
    uint256 public constant FORK = 31337;
    uint256 public constant MAINNET = 1;
    uint256 public constant SEPOLIA = 11155111;
    uint256 public constant HOLESKY = 17000;
    uint256 public constant HOODI = 560048;

    uint256 public immutable depositCap;
    address public immutable owner;
    address public immutable withdrawalNft;
    bool public paused;
    uint256 public assets;
    mapping(address => uint256) public shareBalance;
    mapping(uint256 => address) public nftDepositor;

    error Unreadable();
    error Paused();
    error DepositCap();
    error NotOwner();
    error WithdrawalNftRefused();

    interface IWithdrawalNft {
        function transferFrom(address from, address to, uint256 tokenId) external;
    }

    constructor(uint256 cap, address owner_, address withdrawalNft_) {
        if (cap == 0 || owner_ == address(0)) revert Unreadable();
        depositCap = cap;
        owner = owner_;
        withdrawalNft = withdrawalNft_;
        paused = true;
    }

    function pause() external {
        if (msg.sender != owner) revert NotOwner();
        paused = true;
    }

    function unpause() external {
        if (msg.sender != owner) revert NotOwner();
        paused = false;
    }

    function deposit() external payable {
        if (paused) revert Paused();
        if (msg.value == 0) revert Unreadable();
        if (assets + msg.value > depositCap) revert DepositCap();
        assets += msg.value;
        shareBalance[msg.sender] += msg.value;
    }

    function withdraw(uint256 shares) external {
        if (paused) revert Paused();
        if (shares == 0 || shareBalance[msg.sender] < shares) revert Unreadable();
        shareBalance[msg.sender] -= shares;
        assets -= shares;
        (bool ok,) = msg.sender.call{value: shares}("");
        if (!ok) revert Unreadable();
    }

    function transferShares(address to, uint256 amount) external {
        if (paused) revert Paused();
        if (to == address(0) || amount == 0 || shareBalance[msg.sender] < amount) revert Unreadable();
        shareBalance[msg.sender] -= amount;
        shareBalance[to] += amount;
    }

    function depositWithdrawalNft(uint256 tokenId) external {
        if (paused) revert Paused();
        _nftChain();
        if (withdrawalNft == address(0) || tokenId == 0) revert Unreadable();
        if (nftDepositor[tokenId] != address(0)) revert Unreadable();
        IWithdrawalNft(withdrawalNft).transferFrom(msg.sender, address(this), tokenId);
        nftDepositor[tokenId] = msg.sender;
    }

    function transferWithdrawalNft(address to, uint256 tokenId) external {
        if (paused) revert Paused();
        _nftChain();
        if (to == address(0)) revert Unreadable();
        if (nftDepositor[tokenId] != msg.sender) revert NotOwner();
        nftDepositor[tokenId] = to;
        IWithdrawalNft(withdrawalNft).transferFrom(address(this), to, tokenId);
    }

    function _nftChain() internal view {
        if (block.chainid == MAINNET) revert WithdrawalNftRefused();
        if (
            block.chainid != FORK && block.chainid != SEPOLIA && block.chainid != HOLESKY
                && block.chainid != HOODI
        ) revert WithdrawalNftRefused();
    }
}
