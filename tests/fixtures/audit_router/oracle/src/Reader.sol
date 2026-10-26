// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

interface Aggregator {
    function latestRoundData() external view returns (
        uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound
    );
}

contract Reader {
    Aggregator feed;

    function price() external view returns (int256) {
        (bool ok, bytes memory data) = address(feed).staticcall(
            abi.encodeWithSelector(Aggregator.latestRoundData.selector)
        );
        require(ok, "oracle call failed");
        (, int256 answer,,,) = abi.decode(data, (uint80, int256, uint256, uint256, uint80));
        return answer;
    }
}
