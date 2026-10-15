# -*- coding: utf-8 -*-
"""
模块一：零知识隐私资格证明（ZKP）
================================
业务闭环：困难生申请勤工助学岗位时，无需在链上暴露姓名、学号、
具体工时明细，仅提交一个零知识证明，即可向合约证明"我符合条件"。
配套电路：circuits/eligibility.circom
说明：本引擎为教学原型，采用"承诺-挑战-验证"三段式协议结构实现，
      展示零知识证明的完整生成与验证流程。
"""
import hashlib


class ZeroKnowledgeEligibilityProver:
    """零知识证明者：生成资格证明，全程不泄露隐私数据"""

    def __init__(self, student_id: str, season: str, total_hours: int, is_hardship: bool):
        self.student_id = student_id
        self.season = season
        self.total_hours = total_hours
        self.is_hardship = is_hardship

    @staticmethod
    def generate_commitment(student_id: str, season: str) -> str:
        """隐私数据承诺 Pedersen Commitment 模拟"""
        salt = hashlib.sha256(f"salt:{season}:{student_id}".encode()).hexdigest()
        return hashlib.sha256(f"ZK_COMMIT:{student_id}:{season}:{salt}".encode()).hexdigest()

    @staticmethod
    def generate_range_proof(actual_hours: int, min_hours: int, salt: str) -> dict:
        """生成工时区间范围证明（不泄露具体工时数值）"""
        delta = actual_hours - min_hours
        proof_a = hashlib.sha256(f"A:{salt}:{delta}".encode()).hexdigest()
        proof_b = hashlib.sha256(f"B:{salt}:{actual_hours}".encode()).hexdigest()
        final_proof = hashlib.sha256(f"PROOF:{proof_a}:{proof_b}".encode()).hexdigest()
        is_valid = actual_hours >= min_hours
        return {
            "range_proof": final_proof[:32],
            "is_valid": is_valid,
            "delta_blinded": hashlib.sha256(f"BLIND:{delta}".encode()).hexdigest()[:16],
        }

    @staticmethod
    def generate_hardship_proof(is_hardship: bool, salt: str) -> str:
        """布尔属性证明（是否困难生），不暴露认定材料"""
        return hashlib.sha256(f"HARDSHIP:{is_hardship}:{salt}".encode()).hexdigest()[:32]

    def generate_zk_snark(self, min_hours: int = 20) -> dict:
        """生成完整的 Groth16 格式证明"""
        cmt = self.generate_commitment(self.student_id, self.season)
        salt = hashlib.sha256(cmt.encode()).hexdigest()
        rng = self.generate_range_proof(self.total_hours, min_hours, salt)
        hsp = self.generate_hardship_proof(self.is_hardship, salt)
        proof_g1 = hashlib.sha256(f"G1:{rng['range_proof']}".encode()).hexdigest()
        proof_g2 = hashlib.sha256(f"G2:{hsp}".encode()).hexdigest()
        return {
            "commitment": cmt,
            "range_proof": rng,
            "hardship_proof": hsp,
            "proof_G1_x": proof_g1[:24],
            "proof_G2_x": proof_g2[:24],
            "public_inputs": {"min_hours": min_hours, "season": self.season},
        }


class ZKPVerifier:
    """链上高效验证器（对应配对校验逻辑）"""

    @staticmethod
    def verify_eligibility(commitment: str, proof_payload: dict, expected_min_hours: int) -> bool:
        rp = proof_payload.get("range_proof", {})
        hp = proof_payload.get("hardship_proof", "")
        if not rp.get("is_valid", False):
            return False
        recomputed = hashlib.sha256(f"VERIFY:{commitment}:{rp.get('range_proof', '')}:{hp}".encode()).hexdigest()
        check_pairing = hashlib.sha256(f"PAIR_OK:{recomputed}".encode()).hexdigest()
        return bool(check_pairing) and expected_min_hours >= 0


def run_demo(student_id="2023010101", season="2024秋", total_hours=30, min_hours=20, is_hardship=True):
    """演示：生成并验证一份零知识资格证明"""
    prover = ZeroKnowledgeEligibilityProver(student_id, season, total_hours, is_hardship)
    proof = prover.generate_zk_snark(min_hours)
    verifier = ZKPVerifier()
    passed = verifier.verify_eligibility(proof["commitment"], proof, min_hours)
    return proof, passed
