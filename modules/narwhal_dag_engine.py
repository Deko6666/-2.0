# -*- coding: utf-8 -*-
"""
模块五：DAG 异步共识打卡存证（Narwhal）
======================================
业务闭环：传统单链结构在高峰期打卡确认延迟高，引入 Narwhal 异步
共识机制，将大量并发打卡交易打包为有向无环图（DAG）异步并行出块，
在保证安全的前提下大幅提升吞吐量（TPS）。
说明：本引擎为教学原型，以 Python 对象模拟 DAG 顶点的构建、
      水位线推进与法定人数确认流程。
"""


class DagVertex:
    def __init__(self, creator: str, round_num: int, transactions: list):
        self.creator = creator
        self.round_num = round_num
        self.transactions = transactions
        self.parents = []
        self.digest = self._compute_digest()

    def _compute_digest(self) -> str:
        raw = f"{self.creator}:{self.round_num}:{len(self.transactions)}:{sorted(self.transactions)[:2]}"
        return hash(raw)

    def add_parent(self, parent_vertex: "DagVertex"):
        self.parents.append(parent_vertex.digest)

    def to_dict(self) -> dict:
        return {
            "creator": self.creator,
            "round": self.round_num,
            "tx_count": len(self.transactions),
            "parent_digests": self.parents,
            "digest": self.digest,
        }


class NarwhalDagMempool:
    """Narwhal 交易记忆池与 DAG 主引擎"""

    def __init__(self, validator_ids: list):
        self.validators = validator_ids
        self.dag_graph = {}
        self.committed_watermark = -1

    def submit_clockin_transaction(self, student_id: str, timestamp: str) -> str:
        return f"clock_in:{student_id}:{timestamp}"

    def propose_dag_blocks(self, mempool: list, rounds: int = 3) -> dict:
        total_vertices = 0
        committed = 0
        for rnd in range(rounds):
            round_vertices = []
            for i, validator in enumerate(self.validators):
                chunk_txs = mempool[i::len(self.validators)]
                vertex = DagVertex(creator=validator, round_num=rnd, transactions=chunk_txs)
                if rnd > 0 and self.dag_graph.get(rnd - 1):
                    vertex.add_parent(self.dag_graph[rnd - 1][i])
                round_vertices.append(vertex)
                total_vertices += 1
            self.dag_graph[rnd] = round_vertices
            quorum = (len(self.validators) * 2) // 3 + 1
            if len(round_vertices) >= quorum:
                self.committed_watermark = rnd
                committed += sum(len(v.transactions) for v in round_vertices)
        return {
            "total_vertices": total_vertices,
            "committed_rounds": self.committed_watermark + 1,
            "finalized_txs": committed,
            "quorum_size": (len(self.validators) * 2) // 3 + 1,
            "is_finalized": self.committed_watermark >= rounds - 1,
        }


def run_demo(rounds=3, students=12):
    """演示：一批打卡交易经 DAG 共识确权"""
    mem_pool = NarwhalDagMempool(validator_ids=["val_1", "val_2", "val_3", "val_4", "val_5"])
    txs = [mem_pool.submit_clockin_transaction(f"stu_{i:03d}", f"2024-11-25 08:0{i % 6}:00") for i in range(students)]
    report = mem_pool.propose_dag_blocks(txs, rounds=rounds)
    report["tx_submitted"] = students
    return report
