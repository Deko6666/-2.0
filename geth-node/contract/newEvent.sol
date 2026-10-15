// SPDX-License-Identifier: GPL-3.0
pragma solidity >=0.8.2 <0.9.0;

contract newEvent {
enum Grade {
    A,
    B,
    C,
    D,
    E
}


    event jhenevt(string indexed name, int256 indexed money);

    function testevent(string memory _name, int256 _money) public {
        emit jhenevt(_name, _money);
    }
}
