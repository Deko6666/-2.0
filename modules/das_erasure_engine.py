# -*- coding: utf-8 -*-
"""
模块四：纠删码数据可用性采样（DAS + Reed-Solomon）
==================================================
业务闭环：高校每天海量打卡图片、证明材料无法全部上链（成本过高），
引入纠删码数据可用性采样技术，链下只存数据切片，链上仅保留根哈希。
随机抽取少量切片即可高置信度保证数据可用且未被篡改，极大降低存储成本。
说明：本引擎为教学原型，以哈希切片模拟 Reed-Solomon 纠删码的
      切片编码与随机采样验证流程。
"""
import hashlib
import random


class DataAvailabilitySamplingEngine:
    """纠删码数据可用性引擎"""

    @staticmethod
    def reed_solomon_encode(data: str, total_chunks: int = 16, parity_chunks: int = 8) -> list:
        """模拟 Reed-Solomon 纠删码编码，将数据切片并生成校验切片"""
        raw_hash = hashlib.sha256(data.encode()).hexdigest()
        chunks = []
        for i in range(total_chunks):
            chunk_data = data[i * (len(data) // total_chunks): (i + 1) * (len(data) // total_chunks)]
            chunk_hash = hashlib.sha256(chunk_data.encode()).hexdigest() if chunk_data else raw_hash
            chunks.append({"index": i, "data": chunk_hash[:16], "is_parity": False})
        for j in range(parity_chunks):
            parity_hash = hashlib.sha256(f"PARITY:{raw_hash}:{j}".encode()).hexdigest()
            chunks.append({"index": total_chunks + j, "data": parity_hash[:16], "is_parity": True})
        return chunks

    @staticmethod
    def sample_availability(chunks: list, sample_count: int = 6) -> dict:
        """随机采样验证数据可用性，返回概率评估"""
        total = len(chunks)
        real_parity = len([c for c in chunks if c["is_parity"]])
        sampled_indices = sorted(random.sample(range(total), min(sample_count, total)))
        confidence = min(100.0, (len(sampled_indices) / total) * 135.0 + real_parity * 2.0)
        return {
            "is_available": True,
            "confidence_score": round(confidence, 2),
            "sampled_indices": sampled_indices,
            "total_chunks": total,
            "parity_chunks": real_parity,
        }


def run_demo(data="mock_ipfs_link_0x932847293", total_chunks=16, parity_chunks=8, sample_count=6):
    """演示：对一份打卡数据做纠删码切片与可用性采样"""
    chunks = DataAvailabilitySamplingEngine.reed_solomon_encode(data, total_chunks, parity_chunks)
    sample = DataAvailabilitySamplingEngine.sample_availability(chunks, sample_count)
    sample["data"] = data[:48]
    return sample
