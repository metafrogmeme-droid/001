// SPDX-License-Identifier: BUSL-1.1
pragma solidity 0.8.37;

// Test doubles. Not deployed, not a production oracle, not a mainnet token.

contract ScriptedReadings {
    uint64 public markPx;
    uint64 public markAt;
    uint64 public oracleP;
    uint64 public oracleAt;
    int64 public szi;
    bool public posReadable;

    function setMark(uint64 p, uint64 t) external {
        markPx = p;
        markAt = t;
    }

    function setOracle(uint64 p, uint64 t) external {
        oracleP = p;
        oracleAt = t;
    }

    function setPos(int64 s, bool r) external {
        szi = s;
        posReadable = r;
    }

    function mark(uint32) external view returns (uint64, uint64) {
        return (markPx, markAt);
    }

    function oraclePx(uint32) external view returns (uint64, uint64) {
        return (oracleP, oracleAt);
    }

    function positionSzi(address, uint32) external view returns (int64, bool) {
        return (szi, posReadable);
    }
}

contract TestToken {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 n) external {
        balanceOf[to] += n;
    }

    function approve(address s, uint256 n) external returns (bool) {
        allowance[msg.sender][s] = n;
        return true;
    }

    function transferFrom(address f, address t, uint256 n) external returns (bool) {
        require(balanceOf[f] >= n && allowance[f][msg.sender] >= n);
        allowance[f][msg.sender] -= n;
        balanceOf[f] -= n;
        balanceOf[t] += n;
        return true;
    }

    function transfer(address t, uint256 n) external returns (bool) {
        require(balanceOf[msg.sender] >= n);
        balanceOf[msg.sender] -= n;
        balanceOf[t] += n;
        return true;
    }
}

contract SilentToken {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 n) external {
        balanceOf[to] += n;
    }

    function approve(address s, uint256 n) external {
        allowance[msg.sender][s] = n;
    }

    function transferFrom(address f, address t, uint256 n) external {
        require(balanceOf[f] >= n && allowance[f][msg.sender] >= n);
        allowance[f][msg.sender] -= n;
        balanceOf[f] -= n;
        balanceOf[t] += n;
    }

    function transfer(address t, uint256 n) external {
        require(balanceOf[msg.sender] >= n);
        balanceOf[msg.sender] -= n;
        balanceOf[t] += n;
    }
}

contract FeeToken {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;

    function mint(address to, uint256 n) external {
        balanceOf[to] += n;
    }

    function approve(address s, uint256 n) external returns (bool) {
        allowance[msg.sender][s] = n;
        return true;
    }

    function transferFrom(address f, address t, uint256 n) external returns (bool) {
        require(balanceOf[f] >= n && n > 0);
        balanceOf[f] -= n;
        balanceOf[t] += n - 1;
        return true;
    }

    function transfer(address t, uint256 n) external returns (bool) {
        require(balanceOf[msg.sender] >= n);
        balanceOf[msg.sender] -= n;
        balanceOf[t] += n;
        return true;
    }
}

contract ReenterToken {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    address public escrow;
    bool public armed;
    bool public innerOk;
    bytes public innerRet;
    bool private busy;

    function setEscrow(address e) external {
        escrow = e;
    }

    function arm(bool v) external {
        armed = v;
    }

    function mint(address to, uint256 n) external {
        balanceOf[to] += n;
    }

    function approve(address s, uint256 n) external returns (bool) {
        allowance[msg.sender][s] = n;
        return true;
    }

    function transfer(address t, uint256 n) external returns (bool) {
        require(balanceOf[msg.sender] >= n);
        balanceOf[msg.sender] -= n;
        balanceOf[t] += n;
        return true;
    }

    function transferFrom(address f, address t, uint256 n) external returns (bool) {
        require(balanceOf[f] >= n && allowance[f][msg.sender] >= n);
        allowance[f][msg.sender] -= n;
        balanceOf[f] -= n;
        balanceOf[t] += n;
        if (armed && !busy) {
            busy = true;
            allowance[address(this)][escrow] = 1000;
            (bool ok, bytes memory ret) = escrow.call(
                abi.encodeWithSignature(
                    "open(uint8,uint8,uint32,uint256,uint256,uint256,uint256,uint256,uint256,uint256,uint256)",
                    uint8(2),
                    uint8(1),
                    uint32(0),
                    uint256(100000000),
                    uint256(0),
                    uint256(100000000),
                    uint256(0),
                    uint256(10000000),
                    uint256(60),
                    uint256(1900000000),
                    uint256(1000)
                )
            );
            innerOk = ok;
            innerRet = ret;
            busy = false;
        }
        return true;
    }
}

contract SettleToken {
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(address => uint256)) public allowance;
    address public escrow;
    uint256 public reenterId;
    bool public innerOk;
    bytes public innerRet;
    bool private busy;

    function setEscrow(address e) external {
        escrow = e;
    }

    function arm(uint256 id) external {
        reenterId = id;
    }

    function mint(address to, uint256 n) external {
        balanceOf[to] += n;
    }

    function approve(address s, uint256 n) external returns (bool) {
        allowance[msg.sender][s] = n;
        return true;
    }

    function transferFrom(address f, address t, uint256 n) external returns (bool) {
        require(balanceOf[f] >= n && allowance[f][msg.sender] >= n);
        allowance[f][msg.sender] -= n;
        balanceOf[f] -= n;
        balanceOf[t] += n;
        return true;
    }

    function transfer(address t, uint256 n) external returns (bool) {
        require(balanceOf[msg.sender] >= n);
        balanceOf[msg.sender] -= n;
        balanceOf[t] += n;
        if (reenterId != 0 && !busy) {
            busy = true;
            (bool ok, bytes memory ret) = escrow.call(abi.encodeWithSignature("settle(uint256)", reenterId));
            innerOk = ok;
            innerRet = ret;
            busy = false;
        }
        return true;
    }
}

contract PxPrecompile {
    uint64 immutable px;

    constructor(uint64 p) {
        px = p;
    }

    fallback() external {
        uint64 v = px;
        assembly {
            mstore(0x00, v)
            return(0x00, 0x20)
        }
    }
}

contract PosPrecompile {
    int64 immutable szi;

    constructor(int64 s) {
        szi = s;
    }

    fallback() external {
        int64 s = szi;
        assembly {
            mstore(0x00, s)
            return(0x00, 0x20)
        }
    }
}
