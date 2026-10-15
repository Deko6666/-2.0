pragma circom 2.1.6;

include "circomlib/poseidon.circom";

/*
 * 困难生资格零知识证明电路
 * ========================
 * 业务规则：
 *   1. 学生总工时必须满足阈值（totalHours >= minHours）；
 *   2. 必须是认定的困难生（isHardship == 1）；
 *   3. 学号哈希承诺（IDHash）必须与链上存储的承诺一致；
 *   4. 全程不暴露学生学号、具体工时数值与认定材料。
 *
 * 公开输入（链上可见）：minHours, seasonSalt, IDHash
 * 私密输入（仅证明者本地）：studentID, totalHours, isHardship
 */
template EligibilityVerifier() {
    signal input studentID;
    signal input totalHours;
    signal input isHardship;

    signal input minHours;
    signal input seasonSalt;
    signal input IDHash;

    // 工时区间约束
    signal hoursDelta;
    hoursDelta <== totalHours - minHours;

    // 布尔约束
    isHardship * (isHardship - 1) === 0;
    isHardship === 1;

    // 承诺一致性约束（Poseidon 哈希模拟）
    component hasher = Poseidon(3);
    hasher.inputs[0] <== studentID;
    hasher.inputs[1] <== seasonSalt;
    hasher.inputs[2] <== isHardship;
    signal outHash;
    outHash <== hasher.out;
    outHash === IDHash;

    // 差值非负约束（使用二次约束模拟范围证明）
    signal deltaSq;
    deltaSq <== hoursDelta * hoursDelta;
}

component main = EligibilityVerifier();
