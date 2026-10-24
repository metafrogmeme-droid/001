// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

/// @notice Equity stand-in. The value is immutable so etching the runtime
///         code at the precompile address keeps it. Not part of the vault.
contract MockEquity {
    int256 public immutable value;

    constructor(int256 value_) {
        value = value_;
    }

    fallback() external {
        int256 v = value;
        assembly {
            mstore(0x00, v)
            return(0x00, 0x20)
        }
    }
}

contract MockEquityFail {
    fallback() external {
        revert("unreadable");
    }
}

contract MockAsset {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address to, uint256 amount) external returns (bool) {
        require(balanceOf[msg.sender] >= amount, "balance");
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        require(allowance[from][msg.sender] >= amount, "allowance");
        require(balanceOf[from] >= amount, "balance");
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}

contract MockCore {
    uint256 public hits;

    fallback() external {
        hits += 1;
    }
}

contract MockCoreFail {
    fallback() external {
        revert("no");
    }
}
