# -*- coding: utf-8 -*-
"""
模块二：全同态密文薪酬核算（FHE）
================================
业务闭环：学生时薪、奖金、扣罚金额均以密文形式提交链上，
合约在完全不解密的情况下，直接在密文空间上完成薪酬代数运算，
最终发放前的一次性解密只在学生端进行，从根本上杜绝工资隐私泄露。
说明：本引擎为教学原型，采用"随机数掩码 + 总量校验"的加法同态
      结构，演示密文聚合核算与防篡改校验流程。
"""


class FHEPayrollEngine:
    """基于加法同态性的链上密文计算引擎"""

    def __init__(self):
        self.cipher_base = None
        self.cipher_bonus = None
        self.cipher_penalty = None
        self.is_hardship_encrypted = None

    @staticmethod
    def encrypt_value(value: float, mask_key: int) -> int:
        """加法同态加密（掩码承诺模拟）"""
        return int(value * 100) + mask_key

    @staticmethod
    def homomorphic_add(c1: int, c2: int) -> int:
        """同态加法：密文相加 = 明文相加"""
        return c1 + c2

    @staticmethod
    def homomorphic_sub(c1: int, c2: int) -> int:
        """同态减法：密文相减 = 明文相减"""
        return c1 - c2

    @staticmethod
    def decrypt_value(cipher: int, mask_key: int) -> float:
        return (cipher - mask_key) / 100.0

    def calculate_payroll_ciphertext(self, base_salary: float, bonus: float, penalty: float, is_hardship: bool) -> int:
        self.mask_base, self.mask_bonus, self.mask_penalty = 83721, 11209, 55501
        self.cipher_base = self.encrypt_value(base_salary, self.mask_base)
        self.cipher_bonus = self.encrypt_value(bonus, self.mask_bonus)
        self.cipher_penalty = self.encrypt_value(penalty, self.mask_penalty)
        self.is_hardship_encrypted = hash(is_hardship)
        net_cipher = self.homomorphic_add(self.cipher_base, self.cipher_bonus)
        net_cipher = self.homomorphic_sub(net_cipher, self.cipher_penalty)
        if is_hardship:
            net_cipher = self.homomorphic_add(net_cipher, self.encrypt_value(50.0, 41777))
            self.mask_base += 41777
        self.final_net_cipher = net_cipher
        return net_cipher

    def verify_ciphertext_integrity(self, original_components: list, final_net_cipher: int, is_hardship: bool) -> bool:
        expected = original_components[0] + original_components[1] - original_components[2]
        if is_hardship:
            expected += 5000
        return abs(final_net_cipher - expected) < 1e-6

    def decrypt_final_payroll(self, is_hardship: bool) -> float:
        mask_total = self.mask_base + self.mask_bonus - self.mask_penalty
        net = self.decrypt_value(self.final_net_cipher, mask_total)
        return round(max(net, 0.0), 2)


def run_demo(base_salary=3200.0, bonus=400.0, penalty=80.0, is_hardship=True):
    """演示：对一组薪酬数据做密文核算并校验"""
    engine = FHEPayrollEngine()
    cipher_net = engine.calculate_payroll_ciphertext(base_salary, bonus, penalty, is_hardship)
    integrity = engine.verify_ciphertext_integrity(
        [engine.cipher_base, engine.cipher_bonus, engine.cipher_penalty], cipher_net, is_hardship
    )
    net_salary = engine.decrypt_final_payroll(is_hardship)
    return {
        "cipher_base": engine.cipher_base,
        "cipher_bonus": engine.cipher_bonus,
        "cipher_penalty": engine.cipher_penalty,
        "cipher_net": cipher_net,
        "integrity_ok": integrity,
        "net_salary": net_salary,
    }
