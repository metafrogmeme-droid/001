// SPDX-License-Identifier: BUSL-1.1
pragma solidity ^0.8.24;

import "CGC.sol";

contract FactStub {
    bool public readable;
    bool public violation;
    bool public boom;

    function set(bool readable_, bool violation_) external {
        readable = readable_;
        violation = violation_;
        boom = false;
    }

    function setBoom() external {
        boom = true;
    }

    function readBreach(bytes32, uint8) external view returns (bool, bool) {
        require(!boom, "unreadable");
        return (readable, violation);
    }
}

contract SystemStub {
    bool internal _paused;
    bool internal _retired;
    uint32 internal _gameType;
    bool internal _boom;

    function set(bool paused_, bool retired_, uint32 gameType_) external {
        _paused = paused_;
        _retired = retired_;
        _gameType = gameType_;
        _boom = false;
    }

    function setBoom() external {
        _boom = true;
    }

    function paused() external view returns (bool) {
        require(!_boom, "unreadable");
        return _paused;
    }

    function retired() external view returns (bool) {
        require(!_boom, "unreadable");
        return _retired;
    }

    function respectedGameType() external view returns (uint32) {
        require(!_boom, "unreadable");
        return _gameType;
    }
}

contract FactoryStub {
    struct Row {
        uint32 gameType;
        uint64 timestamp;
        address proxy;
        bool set;
    }

    mapping(uint256 => Row) public rows;

    function set(uint256 index, uint32 gameType_, uint64 timestamp, address proxy) external {
        rows[index] = Row(gameType_, timestamp, proxy, true);
    }

    function gameAtIndex(uint256 index) external view returns (uint32, uint64, address) {
        Row storage row = rows[index];
        require(row.set, "missing game");
        return (row.gameType, row.timestamp, row.proxy);
    }
}

contract GameStub {
    uint8 public status;
    bytes32 public rootClaim;
    uint64 public resolvedAt;

    function set(uint8 status_, bytes32 root, uint64 resolved) external {
        status = status_;
        rootClaim = root;
        resolvedAt = resolved;
    }
}

contract UnderlyingStub {
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
        balanceOf[msg.sender] -= amount;
        balanceOf[to] += amount;
        return true;
    }

    function transferFrom(address from, address to, uint256 amount) external returns (bool) {
        allowance[from][msg.sender] -= amount;
        balanceOf[from] -= amount;
        balanceOf[to] += amount;
        return true;
    }
}

/// Reports success and moves nothing. A mint against it must revert.
contract ShortUnderlying {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 amount) external {
        balanceOf[to] += amount;
    }

    function approve(address spender, uint256 amount) external returns (bool) {
        allowance[msg.sender][spender] = amount;
        return true;
    }

    function transfer(address, uint256) external pure returns (bool) {
        return true;
    }

    function transferFrom(address, address, uint256) external pure returns (bool) {
        return true;
    }
}

/// Receives the refund and tries to take it twice.
contract Greedy {
    CGCBrake public brake;
    uint256 public id;
    bool public reentered;

    function open(address brake_, bytes32 asset, uint8 claimType, bytes32 evidence) external payable {
        brake = CGCBrake(brake_);
        id = brake.brake{value: msg.value}(asset, claimType, evidence);
    }

    receive() external payable {
        if (!reentered && address(brake) != address(0)) {
            reentered = true;
            try brake.refundNoFinalRoot(id) {} catch {}
        }
    }
}
