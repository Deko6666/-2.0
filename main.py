import os
import shutil
import time
import threading
import json
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import create_engine, text
from web3 import Web3

# ======================== 环境初始化 ========================

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

db_password = "123456"
DB_URL = f"mysql+pymysql://root:{db_password}@127.0.0.1:3306/campus_db?charset=utf8mb4"
engine = create_engine(DB_URL)

app = FastAPI(title="高校勤工助学薪酬自动管理系统")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ======================== 初始化数据库表 ========================

def init_database():
    """创建所有必要的数据库表，并修补已有表的缺失列"""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id INT AUTO_INCREMENT PRIMARY KEY,
                username VARCHAR(50) NOT NULL UNIQUE,
                password VARCHAR(50) NOT NULL,
                role ENUM('STUDENT','TEACHER','ADMIN') NOT NULL DEFAULT 'STUDENT',
                nickname VARCHAR(50),
                wallet_address VARCHAR(42),
                avatar VARCHAR(255) DEFAULT 'https://api.dicebear.com/7.x/bottts/svg?seed=1',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS jobs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                chain_job_id INT,
                title VARCHAR(100) NOT NULL,
                department VARCHAR(100) DEFAULT '',
                description TEXT,
                hourly_rate_wei VARCHAR(50) DEFAULT '0',
                salary_wei VARCHAR(50) NOT NULL,
                employer_address VARCHAR(42) NOT NULL,
                student_address VARCHAR(42),
                status ENUM('PENDING','PUBLISHED','APPROVED','TAKEN','COMPLETED','PAID') DEFAULT 'PENDING',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS employments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                chain_employment_id INT,
                job_id INT NOT NULL,
                student_address VARCHAR(42) NOT NULL,
                employer_address VARCHAR(42) NOT NULL,
                status ENUM('PENDING','APPROVED','REJECTED','COMPLETED') DEFAULT 'PENDING',
                total_minutes INT DEFAULT 0,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                approved_at TIMESTAMP NULL,
                FOREIGN KEY (job_id) REFERENCES jobs(id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS clock_ins (
                id INT AUTO_INCREMENT PRIMARY KEY,
                employment_id INT NOT NULL,
                student_address VARCHAR(42) NOT NULL,
                clock_in_time DATETIME NOT NULL,
                clock_out_time DATETIME,
                work_minutes INT DEFAULT 0,
                confirmed BOOLEAN DEFAULT FALSE,
                tx_hash VARCHAR(66),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (employment_id) REFERENCES employments(id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))

        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS settlements (
                id INT AUTO_INCREMENT PRIMARY KEY,
                employment_id INT NOT NULL,
                student_address VARCHAR(42) NOT NULL,
                amount_wei VARCHAR(50) NOT NULL,
                hours_settled_minutes INT NOT NULL,
                tx_hash VARCHAR(66),
                status ENUM('PENDING','CONFIRMED','FAILED') DEFAULT 'PENDING',
                settled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (employment_id) REFERENCES employments(id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))

        # 审计日志表 - 记录所有关键操作，提升公信力
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                action VARCHAR(50) NOT NULL,
                actor VARCHAR(42),
                target VARCHAR(100),
                detail TEXT,
                tx_hash VARCHAR(66),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))

        # 系统配置表 - 自动结算配置
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS system_config (
                config_key VARCHAR(50) PRIMARY KEY,
                config_value VARCHAR(255) NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))

        # 修补已有表：添加可能缺失的列
        for col, col_def in [
            ("department", "VARCHAR(100) DEFAULT ''"),
            ("hourly_rate_wei", "VARCHAR(50) DEFAULT '0'"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE jobs ADD COLUMN {col} {col_def}"))
            except Exception:
                pass

        # 修补users表
        for col, col_def in [
            ("created_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {col_def}"))
            except Exception:
                pass

        # 修补clock_ins表
        for col, col_def in [
            ("chain_clock_in_id", "INT DEFAULT 0"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE clock_ins ADD COLUMN {col} {col_def}"))
            except Exception:
                pass

        # 确保默认管理员存在
        admin = conn.execute(text("SELECT id FROM users WHERE username='admin'")).fetchone()
        if not admin:
            conn.execute(text("""
                INSERT INTO users (username, password, role, nickname)
                VALUES ('admin', '123456', 'ADMIN', '系统管理员')
            """))
        conn.commit()

init_database()

# ======================== 审计日志 ========================

def log_audit(action: str, actor: str = "", target: str = "", detail: str = "", tx_hash: str = ""):
    """记录审计日志，提升系统公信力"""
    try:
        with engine.connect() as conn:
            conn.execute(text("""
                INSERT INTO audit_logs (action, actor, target, detail, tx_hash)
                VALUES (:a, :ac, :t, :d, :tx)
            """), {"a": action, "ac": actor, "t": target, "d": detail, "tx": tx_hash})
            conn.commit()
    except Exception as e:
        print(f"[AUDIT ERROR] {e}")

def get_config(key: str, default: str = "") -> str:
    try:
        with engine.connect() as conn:
            r = conn.execute(text("SELECT config_value FROM system_config WHERE config_key=:k"), {"k": key}).fetchone()
            return r[0] if r else default
    except Exception:
        return default

def set_config(key: str, value: str):
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO system_config (config_key, config_value) VALUES (:k, :v)
            ON DUPLICATE KEY UPDATE config_value=:v
        """), {"k": key, "v": value})
        conn.commit()

# ======================== 区块链交互 ========================

RPC_URL = "http://127.0.0.1:8545"

def get_web3():
    return Web3(Web3.HTTPProvider(RPC_URL))

def get_contract_address() -> str:
    # 优先从部署生成的配置文件读取
    try:
        if os.path.exists("contract_address.txt"):
            with open("contract_address.txt", "r") as f:
                addr = f.read().strip()
                if addr and addr.startswith("0x"):
                    return addr
    except Exception:
        pass
    return get_config("contract_address", "0x83432380f1b9bdbc4b4e2ca3a35f8cb4e0154c6e")

# 链上岗位查询 ABI
JOB_QUERY_ABI = json.loads('''[
    {"inputs":[],"name":"jobCount","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"type":"uint256"}],"name":"getJob","outputs":[{"type":"tuple","components":[{"type":"uint256"},{"type":"string"},{"type":"string"},{"type":"uint256"},{"type":"address"},{"type":"uint256"},{"type":"uint256"},{"type":"uint8"},{"type":"uint256"}]}],"stateMutability":"view","type":"function"}
]''')

def repair_chain_job_ids():
    """修复数据库中 chain_job_id=0 的岗位记录（从链上匹配）"""
    try:
        w3 = get_web3()
        addr = get_contract_address()
        contract = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=JOB_QUERY_ABI)
        job_count = contract.functions.jobCount().call()

        # 从链上读取所有岗位信息
        chain_jobs = {}
        for i in range(1, job_count + 1):
            try:
                job = contract.functions.getJob(i).call()
                # job = (id, title, department, hourlyRate, employer, totalBudget, remainingBudget, status, createdAt)
                key = (job[4].lower(), job[1])  # (employer, title)
                if key not in chain_jobs:
                    chain_jobs[key] = job[0]
            except Exception:
                pass

        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT id, title, employer_address FROM jobs WHERE chain_job_id = 0 OR chain_job_id IS NULL"
            )).fetchall()
            repaired = 0
            for r in rows:
                key = (r[2].lower(), r[1])
                if key in chain_jobs:
                    conn.execute(text("UPDATE jobs SET chain_job_id=:c WHERE id=:i"),
                                 {"c": chain_jobs[key], "i": r[0]})
                    repaired += 1
            conn.commit()
            if repaired > 0:
                print(f"[REPAIR] 已修复 {repaired} 个岗位的链上ID")
                log_audit("CHAIN_ID_REPAIRED", "system", "", f"修复了 {repaired} 个岗位的链上ID")
    except Exception as e:
        print(f"[REPAIR ERROR] {e}")

# 自动结算合约 ABI（仅需用到的函数）
AUTO_SETTLE_ABI = json.loads('''[
    {"inputs":[],"name":"executeAutoSettlement","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[],"name":"isSettlementDue","outputs":[{"type":"bool"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"getSettlementConfig","outputs":[{"type":"uint256"},{"type":"uint256"},{"type":"bool"},{"type":"uint256"},{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"getPendingSettlementIds","outputs":[{"type":"uint256[]"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"type":"uint256"},{"type":"bool"},{"type":"uint256"}],"name":"updateSettlementConfig","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"type":"uint256"}],"name":"getEmployment","outputs":[{"type":"tuple","components":[{"type":"uint256"},{"type":"uint256"},{"type":"address"},{"type":"uint8"},{"type":"uint256"},{"type":"uint256"},{"type":"uint256"}]}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"jobCount","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"employmentCount","outputs":[{"type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"type":"uint256"}],"name":"getClockIn","outputs":[{"type":"tuple","components":[{"type":"uint256"},{"type":"uint256"},{"type":"address"},{"type":"uint256"},{"type":"uint256"},{"type":"uint256"},{"type":"bool"}]}],"stateMutability":"view","type":"function"}
]''')

def call_contract_function(fn_name: str, args: list = None):
    """调用合约视图函数"""
    try:
        w3 = get_web3()
        addr = get_contract_address()
        contract = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=AUTO_SETTLE_ABI)
        fn = getattr(contract.functions, fn_name)
        return fn(*(args or [])).call()
    except Exception as e:
        print(f"[CONTRACT VIEW ERROR] {fn_name}: {e}")
        return None

def send_contract_tx(fn_name: str, args: list = None):
    """发送合约交易（使用geth解锁账户）"""
    try:
        w3 = get_web3()
        accounts = w3.eth.accounts
        if not accounts:
            return None, "无可用账户"
        sender = accounts[0]
        addr = get_contract_address()
        contract = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=AUTO_SETTLE_ABI)
        fn = getattr(contract.functions, fn_name)
        tx = fn(*(args or [])).build_transaction({
            'from': sender,
            'gas': 5000000,
            'gasPrice': w3.eth.gas_price,
            'nonce': w3.eth.get_transaction_count(sender),
        })
        tx_hash = w3.eth.send_transaction(tx)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        return receipt, None
    except Exception as e:
        return None, str(e)

# ======================== 自动结算调度器 ========================

def check_and_execute_auto_settlement():
    """检查并执行自动结算"""
    try:
        auto_enabled = get_config("auto_settlement_enabled", "true")
        if auto_enabled != "true":
            return

        is_due = call_contract_function("isSettlementDue")
        if not is_due:
            return

        pending_ids = call_contract_function("getPendingSettlementIds")

        log_audit("AUTO_SETTLEMENT_TRIGGERED", "system", "", f"自动结算触发，待结算合约数: {len(pending_ids) if pending_ids else 0}")
        receipt, err = send_contract_tx("executeAutoSettlement")
        if receipt and receipt.status == 1:
            _sync_settlements_from_chain()
            log_audit("AUTO_SETTLEMENT_SUCCESS", "system", "", f"自动结算成功 tx: {receipt.transactionHash.hex()}", receipt.transactionHash.hex())
        else:
            log_audit("AUTO_SETTLEMENT_FAILED", "system", "", f"自动结算失败: {err}")
    except Exception as e:
        print(f"[AUTO SETTLEMENT ERROR] {e}")
        log_audit("AUTO_SETTLEMENT_ERROR", "system", "", str(e))

def _sync_settlements_from_chain():
    """从链上同步结算记录到数据库"""
    try:
        w3 = get_web3()
        addr = get_contract_address()
        contract = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=AUTO_SETTLE_ABI)
        employment_count = contract.functions.employmentCount().call()
        with engine.connect() as conn:
            for e_id in range(1, employment_count + 1):
                emp = contract.functions.getEmployment(e_id).call()
                chain_emp_id = emp[0]
                total_hours = emp[4]
                if total_hours == 0:
                    r = conn.execute(text("SELECT id, total_minutes FROM employments WHERE chain_employment_id=:c AND status='APPROVED' AND total_minutes > 0"), {"c": chain_emp_id}).fetchone()
                    if r:
                        job = conn.execute(text("""
                            SELECT j.hourly_rate_wei FROM jobs j
                            JOIN employments e ON e.job_id = j.id
                            WHERE e.id = :e
                        """), {"e": r[0]}).fetchone()
                        hourly_rate = int(job[0]) if job and job[0] else 0
                        amount = (r[1] * hourly_rate) // 60
                        conn.execute(text("""
                            INSERT INTO settlements (employment_id, student_address, amount_wei, hours_settled_minutes, tx_hash, status)
                            SELECT :e, student_address, :a, :h, '', 'CONFIRMED' FROM employments WHERE id=:e
                        """), {"e": r[0], "a": str(amount), "h": r[1]})
                        conn.execute(text("UPDATE employments SET total_minutes=0 WHERE id=:e"), {"e": r[0]})
                        log_audit("SETTLEMENT_SYNCED", "system", f"employment#{r[0]}", f"金额: {Web3.from_wei(amount,'ether')} ETH")
            conn.commit()
    except Exception as e:
        print(f"[SYNC ERROR] {e}")

def auto_settlement_scheduler():
    """后台定时任务：每小时检查一次是否需要自动结算"""
    while True:
        try:
            check_and_execute_auto_settlement()
        except Exception as e:
            print(f"[SCHEDULER ERROR] {e}")
        time.sleep(3600)

_scheduler_thread = threading.Thread(target=auto_settlement_scheduler, daemon=True)
_scheduler_thread.start()
print("[AUTO-SETTLEMENT] 后台调度器已启动")

# 启动时自动修复链上岗位ID
threading.Thread(target=repair_chain_job_ids, daemon=True).start()

# ======================== 数据模型 ========================

class UserLogin(BaseModel):
    username: str
    password: str

class UserRegister(BaseModel):
    username: str
    password: str
    role: str

class ProfileUpdate(BaseModel):
    username: str
    nickname: str
    avatar: str

class JobCreate(BaseModel):
    title: str
    department: str = ""
    description: str = ""
    hourly_rate: str
    salary: str
    employer: str
    chain_job_id: int = 0

class WalletLogin(BaseModel):
    wallet_address: str

class WalletBind(BaseModel):
    username: str
    wallet_address: str

class JobApply(BaseModel):
    job_id: int
    student: str
    chain_employment_id: int = 0

class JobDelete(BaseModel):
    job_id: int

class ResignJob(BaseModel):
    employment_id: int
    student: str

class AdminDeleteUser(BaseModel):
    user_id: int

class RejectClockIn(BaseModel):
    clock_in_id: int

class EmploymentAction(BaseModel):
    employment_id: int

class ClockInData(BaseModel):
    employment_id: int
    student: str
    chain_clock_in_id: int = 0

class ClockOutData(BaseModel):
    clock_in_id: int

class ConfirmHoursData(BaseModel):
    clock_in_id: int

class SettleSalaryData(BaseModel):
    employment_id: int
    tx_hash: str = ""

class AdminUserUpdate(BaseModel):
    user_id: int
    role: str

# ======================== 用户管理 API ========================

@app.post("/api/login")
def login(user: UserLogin):
    with engine.connect() as conn:
        query = text("SELECT id, username, role, wallet_address, nickname, avatar FROM users WHERE username=:u AND password=:p")
        r = conn.execute(query, {"u": user.username, "p": user.password}).fetchone()
        if r:
            return {"success": True, "data": {"id": r[0], "username": r[1], "role": r[2], "wallet_address": r[3], "nickname": r[4], "avatar": r[5]}}
        return {"success": False, "msg": "账号或密码错误"}

@app.post("/api/register")
def register(new_user: UserRegister):
    with engine.connect() as conn:
        check = conn.execute(text("SELECT id FROM users WHERE username=:u"), {"u": new_user.username}).fetchone()
        if check:
            return {"success": False, "msg": "账号已存在"}
        conn.execute(text("INSERT INTO users (username, password, role) VALUES (:u, :p, :r)"),
                     {"u": new_user.username, "p": new_user.password, "r": new_user.role})
        conn.commit()
        r = conn.execute(text("SELECT id, username, role, wallet_address, nickname, avatar FROM users WHERE username=:u"), {"u": new_user.username}).fetchone()
        return {"success": True, "data": {"id": r[0], "username": r[1], "role": r[2], "wallet_address": r[3], "nickname": r[4], "avatar": r[5]}}

@app.post("/api/login_by_wallet")
def login_by_wallet(data: WalletLogin):
    with engine.connect() as conn:
        query = text("""
            SELECT id, username, role, wallet_address, nickname, avatar
            FROM users
            WHERE LOWER(TRIM(wallet_address)) = LOWER(TRIM(:w))
        """)
        r = conn.execute(query, {"w": data.wallet_address}).fetchone()
        if r:
            return {"success": True, "data": {"id": r[0], "username": r[1], "role": r[2], "wallet_address": r[3], "nickname": r[4], "avatar": r[5]}}
        else:
            return {"success": False, "msg": "该钱包尚未绑定账号"}

@app.post("/api/bind_wallet")
def bind_wallet(bind_data: WalletBind):
    with engine.connect() as conn:
        # 检查该钱包是否已被其他用户绑定
        existing = conn.execute(text(
            "SELECT id, username FROM users WHERE LOWER(TRIM(wallet_address)) = LOWER(TRIM(:w)) AND username != :u"
        ), {"w": bind_data.wallet_address, "u": bind_data.username}).fetchone()
        if existing:
            return {"success": False, "msg": f"该钱包已被用户 [{existing[1]}] 绑定，无法重复绑定"}
        query = text("UPDATE users SET wallet_address = :w WHERE username = :u")
        conn.execute(query, {"w": bind_data.wallet_address, "u": bind_data.username})
        conn.commit()
        return {"success": True, "msg": "钱包绑定成功"}

@app.post("/api/upload_avatar")
async def upload_avatar(file: UploadFile = File(...)):
    ext = file.filename.split(".")[-1]
    filename = f"avatar_{int(time.time())}.{ext}"
    save_path = os.path.join(UPLOAD_DIR, filename)
    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"success": True, "url": f"http://127.0.0.1:8000/uploads/{filename}"}

@app.post("/api/update_profile")
def update_profile(data: ProfileUpdate):
    with engine.connect() as conn:
        query = text("UPDATE users SET nickname = :n, avatar = :a WHERE username = :u")
        conn.execute(query, {"n": data.nickname, "a": data.avatar, "u": data.username})
        conn.commit()
        return {"success": True}

# ======================== 岗位管理 API ========================

@app.post("/api/save_job")
def save_job(job: JobCreate):
    with engine.connect() as conn:
        query = text("""
            INSERT INTO jobs (chain_job_id, title, department, description, hourly_rate_wei, salary_wei, employer_address, status)
            VALUES (:cid, :t, :dep, :d, :hr, :s, :e, 'PUBLISHED')
        """)
        conn.execute(query, {"cid": job.chain_job_id, "t": job.title, "dep": job.department, "d": job.description, "hr": job.hourly_rate, "s": job.salary, "e": job.employer})
        conn.commit()
        log_audit("JOB_CREATED", job.employer, job.title, f"部门: {job.department}, 预算: {Web3.from_wei(int(job.salary),'ether')} ETH")
        return {"success": True}

@app.get("/api/public_jobs")
def get_public_jobs():
    with engine.connect() as conn:
        # 先检查 employments 表是否存在，不存在则返回空
        try:
            conn.execute(text("SELECT 1 FROM employments LIMIT 1"))
        except Exception:
            # employments 表不存在，先只查 jobs
            query = text("""
                SELECT id, chain_job_id, title, COALESCE(department,''), COALESCE(hourly_rate_wei,'0'), salary_wei, employer_address, status, COALESCE(description,'')
                FROM jobs WHERE status IN ('PUBLISHED', 'APPROVED') ORDER BY created_at DESC
            """)
            results = conn.execute(query).fetchall()
            return [{
                "id": r[0], "chain_job_id": r[1], "title": r[2], "department": r[3],
                "hourly_rate": str(Web3.from_wei(int(r[4]), 'ether')) if r[4] and r[4] != '0' else '0',
                "salary": str(Web3.from_wei(int(r[5]), 'ether')),
                "employer": r[6], "status": r[7], "description": r[8], "applicant_count": 0
            } for r in results]

        query = text("""
            SELECT j.id, j.chain_job_id, j.title, COALESCE(j.department,''), COALESCE(j.hourly_rate_wei,'0'), j.salary_wei, j.employer_address, j.status, COALESCE(j.description,''),
                   (SELECT COUNT(*) FROM employments WHERE job_id = j.id) as applicant_count
            FROM jobs j
            WHERE j.status IN ('PUBLISHED', 'APPROVED')
            ORDER BY j.created_at DESC
        """)
        results = conn.execute(query).fetchall()
        return [{
            "id": r[0],
            "chain_job_id": r[1],
            "title": r[2],
            "department": r[3],
            "hourly_rate": str(Web3.from_wei(int(r[4]), 'ether')) if r[4] and r[4] != '0' else '0',
            "salary": str(Web3.from_wei(int(r[5]), 'ether')),
            "employer": r[6],
            "status": r[7],
            "description": r[8],
            "applicant_count": r[9]
        } for r in results]

@app.get("/api/job/{job_id}")
def get_job_detail(job_id: int):
    with engine.connect() as conn:
        query = text("SELECT id, title, department, description, hourly_rate_wei, salary_wei, employer_address, status FROM jobs WHERE id=:id")
        r = conn.execute(query, {"id": job_id}).fetchone()
        if not r:
            return {"success": False, "msg": "岗位不存在"}
        return {
            "success": True,
            "data": {
                "id": r[0], "title": r[1], "department": r[2], "description": r[3],
                "hourly_rate": str(Web3.from_wei(int(r[4]), 'ether')) if r[4] and r[4] != '0' else '0',
                "salary": str(Web3.from_wei(int(r[5]), 'ether')),
                "employer": r[6], "status": r[7]
            }
        }

# ======================== 申请与审批 API ========================

@app.post("/api/apply_job")
def apply_job(data: JobApply):
    try:
        with engine.connect() as conn:
            # 检查是否已有进行中的岗位（只能领取一份）
            active = conn.execute(text(
                "SELECT e.id, j.title FROM employments e JOIN jobs j ON e.job_id = j.id WHERE e.student_address=:s AND e.status IN ('PENDING','APPROVED')"
            ), {"s": data.student}).fetchone()
            if active:
                return {"success": False, "msg": f"你已有进行中的岗位[{active[1]}]，请先完成或辞职后再申请新岗位"}

            # 检查是否已申请该岗位
            existing = conn.execute(text(
                "SELECT id FROM employments WHERE job_id=:j AND student_address=:s"
            ), {"j": data.job_id, "s": data.student}).fetchone()
            if existing:
                return {"success": False, "msg": "你已申请过该岗位"}

            # 获取岗位信息
            job = conn.execute(text(
                "SELECT employer_address FROM jobs WHERE id=:j"
            ), {"j": data.job_id}).fetchone()
            if not job:
                return {"success": False, "msg": "岗位不存在"}

            conn.execute(text("""
                INSERT INTO employments (job_id, student_address, employer_address, chain_employment_id, status)
                VALUES (:j, :s, :e, :cid, 'PENDING')
            """), {"j": data.job_id, "s": data.student, "e": job[0], "cid": data.chain_employment_id})
            conn.commit()
            return {"success": True, "msg": "申请已提交，等待教师审批"}
    except Exception as e:
        return {"success": False, "msg": f"系统错误: {str(e)}"}

@app.post("/api/approve_application")
def approve_application(data: EmploymentAction):
    with engine.connect() as conn:
        emp = conn.execute(text(
            "SELECT id, job_id, student_address FROM employments WHERE id=:e AND status='PENDING'"
        ), {"e": data.employment_id}).fetchone()
        if not emp:
            return {"success": False, "msg": "申请不存在或已处理"}

        conn.execute(text("UPDATE employments SET status='APPROVED', approved_at=NOW() WHERE id=:e"),
                     {"e": data.employment_id})
        conn.execute(text("UPDATE jobs SET status='TAKEN', student_address=:s WHERE id=:j"),
                     {"s": emp[2], "j": emp[1]})
        conn.commit()
        log_audit("APPLICATION_APPROVED", "", f"employment#{data.employment_id}", f"学生: {emp[2]}")
        return {"success": True, "msg": "已批准"}

@app.post("/api/reject_application")
def reject_application(data: EmploymentAction):
    with engine.connect() as conn:
        conn.execute(text("UPDATE employments SET status='REJECTED' WHERE id=:e AND status='PENDING'"),
                     {"e": data.employment_id})
        conn.commit()
        log_audit("APPLICATION_REJECTED", "", f"employment#{data.employment_id}", "")
        return {"success": True, "msg": "已拒绝"}

@app.post("/api/delete_job")
def delete_job(data: JobDelete):
    with engine.connect() as conn:
        job = conn.execute(text("SELECT id, status FROM jobs WHERE id=:j"), {"j": data.job_id}).fetchone()
        if not job:
            return {"success": False, "msg": "岗位不存在"}
        # 删除关联的打卡记录和结算记录
        conn.execute(text("""
            DELETE ci FROM clock_ins ci
            JOIN employments e ON ci.employment_id = e.id
            WHERE e.job_id = :j
        """), {"j": data.job_id})
        conn.execute(text("""
            DELETE s FROM settlements s
            JOIN employments e ON s.employment_id = e.id
            WHERE e.job_id = :j
        """), {"j": data.job_id})
        conn.execute(text("DELETE FROM employments WHERE job_id=:j"), {"j": data.job_id})
        conn.execute(text("DELETE FROM jobs WHERE id=:j"), {"j": data.job_id})
        conn.commit()
        return {"success": True, "msg": "岗位已删除"}

@app.post("/api/resign")
def resign(data: ResignJob):
    with engine.connect() as conn:
        emp = conn.execute(text(
            "SELECT id, status FROM employments WHERE id=:e AND student_address=:s"
        ), {"e": data.employment_id, "s": data.student}).fetchone()
        if not emp:
            return {"success": False, "msg": "合约不存在"}
        if emp[1] not in ('PENDING', 'APPROVED'):
            return {"success": False, "msg": "当前状态无法辞职"}

        # 更新合约状态
        conn.execute(text("UPDATE employments SET status='REJECTED' WHERE id=:e"), {"e": data.employment_id})
        # 重新开放岗位
        conn.execute(text("""
            UPDATE jobs SET status='PUBLISHED', student_address=NULL
            WHERE id = (SELECT job_id FROM employments WHERE id=:e)
        """), {"e": data.employment_id})
        conn.commit()
        return {"success": True, "msg": "辞职成功"}

@app.get("/api/my_applications/{wallet_address}")
def get_my_applications(wallet_address: str):
    try:
        with engine.connect() as conn:
            results = conn.execute(text("""
                SELECT e.id, e.job_id, e.status, COALESCE(e.total_minutes,0), e.applied_at, e.approved_at,
                       j.title, COALESCE(j.department,''), COALESCE(j.hourly_rate_wei,'0'), j.employer_address,
                       COALESCE(e.chain_employment_id, 0)
                FROM employments e
                JOIN jobs j ON e.job_id = j.id
                WHERE e.student_address = :w
                ORDER BY e.applied_at DESC
            """), {"w": wallet_address}).fetchall()
            return [{
                "employment_id": r[0], "job_id": r[1], "status": r[2],
                "total_minutes": r[3], "applied_at": str(r[4]), "approved_at": str(r[5]) if r[5] else None,
                "title": r[6], "department": r[7],
                "hourly_rate": str(Web3.from_wei(int(r[8]), 'ether')) if r[8] and r[8] != '0' else '0',
                "employer": r[9],
                "chain_employment_id": r[10]
            } for r in results]
    except Exception as e:
        return []

@app.get("/api/pending_applications/{employer_address}")
def get_pending_applications(employer_address: str):
    try:
        with engine.connect() as conn:
            results = conn.execute(text("""
                SELECT e.id, e.student_address, e.applied_at, j.title, COALESCE(j.department,''), COALESCE(e.chain_employment_id, 0)
                FROM employments e
                JOIN jobs j ON e.job_id = j.id
                WHERE e.employer_address = :w AND e.status = 'PENDING'
                ORDER BY e.applied_at DESC
            """), {"w": employer_address}).fetchall()

            applications = []
            for r in results:
                student = conn.execute(text(
                    "SELECT nickname, avatar FROM users WHERE wallet_address=:w"
                ), {"w": r[1]}).fetchone()
                applications.append({
                    "employment_id": r[0],
                    "student_address": r[1],
                    "applied_at": str(r[2]),
                    "job_title": r[3],
                    "department": r[4],
                    "chain_employment_id": r[5],
                    "student_nickname": student[0] if student else "未知",
                    "student_avatar": student[1] if student else ""
                })
            return applications
    except Exception as e:
        return []

@app.get("/api/pending_clock_ins/{employer_address}")
def get_pending_clock_ins(employer_address: str):
    """获取教师名下所有待确认的打卡记录"""
    try:
        with engine.connect() as conn:
            results = conn.execute(text("""
                SELECT c.id, COALESCE(c.chain_clock_in_id, 0), c.employment_id, c.student_address,
                       c.clock_in_time, c.clock_out_time, c.work_minutes,
                       j.title, COALESCE(j.department,'')
                FROM clock_ins c
                JOIN employments e ON c.employment_id = e.id
                JOIN jobs j ON e.job_id = j.id
                WHERE e.employer_address = :w AND c.confirmed = FALSE AND c.clock_out_time IS NOT NULL
                ORDER BY c.clock_out_time DESC
            """), {"w": employer_address}).fetchall()
            records = []
            for r in results:
                student = conn.execute(text(
                    "SELECT nickname FROM users WHERE wallet_address=:w"
                ), {"w": r[3]}).fetchone()
                records.append({
                    "clock_in_id": r[0], "chain_clock_in_id": r[1], "employment_id": r[2], "student_address": r[3],
                    "clock_in_time": str(r[4]), "clock_out_time": str(r[5]), "work_minutes": r[6],
                    "job_title": r[7], "department": r[8],
                    "student_nickname": student[0] if student else "未知"
                })
            return records
    except Exception as e:
        return []

@app.get("/api/pending_settlements/{employer_address}")
def get_pending_settlements(employer_address: str):
    """获取教师名下有待结算工时的合约"""
    try:
        with engine.connect() as conn:
            results = conn.execute(text("""
                SELECT e.id, e.chain_employment_id, e.student_address, e.total_minutes,
                       j.title, COALESCE(j.department,''), j.hourly_rate_wei
                FROM employments e
                JOIN jobs j ON e.job_id = j.id
                WHERE e.employer_address = :w AND e.status = 'APPROVED' AND e.total_minutes > 0
                ORDER BY e.id DESC
            """), {"w": employer_address}).fetchall()
            records = []
            for r in results:
                student = conn.execute(text(
                    "SELECT nickname FROM users WHERE wallet_address=:w"
                ), {"w": r[2]}).fetchone()
                hourly_rate = int(r[6]) if r[6] else 0
                salary_wei = (r[3] * hourly_rate) // 60
                records.append({
                    "employment_id": r[0], "chain_employment_id": r[1] or 0,
                    "student_address": r[2], "total_minutes": r[3],
                    "job_title": r[4], "department": r[5],
                    "hourly_rate_wei": r[6],
                    "salary_wei": str(salary_wei),
                    "salary_ether": str(Web3.from_wei(salary_wei, 'ether')),
                    "student_nickname": student[0] if student else "未知"
                })
            return records
    except Exception as e:
        return []

# ======================== 工时打卡 API ========================

@app.post("/api/clock_in")
def clock_in(data: ClockInData):
    with engine.connect() as conn:
        emp = conn.execute(text(
            "SELECT id, status FROM employments WHERE id=:e AND student_address=:s"
        ), {"e": data.employment_id, "s": data.student}).fetchone()
        if not emp:
            return {"success": False, "msg": "任职合约不存在"}
        if emp[1] != 'APPROVED':
            return {"success": False, "msg": "申请尚未审批通过"}

        # 检查是否有未结束的打卡
        active = conn.execute(text(
            "SELECT id FROM clock_ins WHERE employment_id=:e AND clock_out_time IS NULL"
        ), {"e": data.employment_id}).fetchone()
        if active:
            return {"success": False, "msg": "你有未结束的打卡记录，请先下班打卡"}

        conn.execute(text("""
            INSERT INTO clock_ins (employment_id, student_address, clock_in_time, chain_clock_in_id)
            VALUES (:e, :s, NOW(), :ccid)
        """), {"e": data.employment_id, "s": data.student, "ccid": data.chain_clock_in_id})
        conn.commit()

        # 获取刚插入的记录ID和上班时间（供前端启动计时器）
        record = conn.execute(text(
            "SELECT id, clock_in_time FROM clock_ins WHERE employment_id=:e ORDER BY id DESC LIMIT 1"
        ), {"e": data.employment_id}).fetchone()

        return {"success": True, "clock_in_id": record[0], "clock_in_time": str(record[1]), "msg": "上班打卡成功"}

@app.post("/api/clock_out")
def clock_out(data: ClockOutData):
    with engine.connect() as conn:
        record = conn.execute(text(
            "SELECT id, chain_clock_in_id, clock_out_time FROM clock_ins WHERE id=:c"
        ), {"c": data.clock_in_id}).fetchone()
        if not record:
            return {"success": False, "msg": "打卡记录不存在"}
        if record[2] is not None:
            return {"success": False, "msg": "已经下班打卡过了"}

        # 工时由服务端按时间戳自动计算，不再信任客户端传值（防虚报）
        # 口径与合约 clockOut 一致：分钟向下取整、上限 480、不足 1 分钟记 0
        work_minutes = -1
        chain_clock_in_id = record[1] or 0

        # 优先取链上值（结算以链上为准，保证 DB 与链上口径一致）
        if chain_clock_in_id > 0:
            chain_record = call_contract_function("getClockIn", [chain_clock_in_id])
            if chain_record is not None:
                work_minutes = int(chain_record[5])  # ClockInRecord.workMinutes

        # 链上不可用时按数据库时间戳兜底
        if work_minutes < 0:
            calc = conn.execute(text("""
                SELECT LEAST(TIMESTAMPDIFF(MINUTE, clock_in_time, NOW()), 480)
                FROM clock_ins WHERE id=:c
            """), {"c": data.clock_in_id}).fetchone()
            work_minutes = max(0, int(calc[0] or 0))

        conn.execute(text("""
            UPDATE clock_ins SET clock_out_time=NOW(), work_minutes=:m WHERE id=:c
        """), {"m": work_minutes, "c": data.clock_in_id})
        conn.commit()
        if work_minutes == 0:
            return {"success": True, "work_minutes": 0, "msg": "下班打卡成功，本次不足1分钟工时记0分钟（系统自动计时）"}
        return {"success": True, "work_minutes": work_minutes, "msg": f"下班打卡成功，本次工时{work_minutes}分钟（系统自动计时）"}

@app.get("/api/clock_ins/{employment_id}")
def get_clock_ins(employment_id: int):
    with engine.connect() as conn:
        results = conn.execute(text("""
            SELECT id, COALESCE(chain_clock_in_id, 0), clock_in_time, clock_out_time, work_minutes, confirmed
            FROM clock_ins
            WHERE employment_id = :e
            ORDER BY clock_in_time DESC
        """), {"e": employment_id}).fetchall()
        return [{
            "id": r[0],
            "chain_clock_in_id": r[1],
            "clock_in_time": str(r[2]),
            "clock_out_time": str(r[3]) if r[3] else None,
            "work_minutes": r[4],
            "confirmed": bool(r[5])
        } for r in results]

@app.post("/api/confirm_hours")
def confirm_hours(data: ConfirmHoursData):
    with engine.connect() as conn:
        record = conn.execute(text(
            "SELECT id, employment_id, work_minutes, confirmed FROM clock_ins WHERE id=:c"
        ), {"c": data.clock_in_id}).fetchone()
        if not record:
            return {"success": False, "msg": "打卡记录不存在"}
        if record[3]:
            return {"success": False, "msg": "已确认过"}

        conn.execute(text("UPDATE clock_ins SET confirmed=TRUE WHERE id=:c"), {"c": data.clock_in_id})
        conn.execute(text("UPDATE employments SET total_minutes = total_minutes + :m WHERE id=:e"),
                     {"m": record[2], "e": record[1]})
        conn.commit()
        log_audit("HOURS_CONFIRMED", "", f"clock_in#{data.clock_in_id}", f"确认工时: {record[2]}分钟")
        return {"success": True, "msg": f"已确认{record[2]}分钟工时"}

@app.post("/api/reject_clock_in")
def reject_clock_in(data: RejectClockIn):
    with engine.connect() as conn:
        record = conn.execute(text(
            "SELECT id, employment_id, work_minutes FROM clock_ins WHERE id=:c AND confirmed=FALSE"
        ), {"c": data.clock_in_id}).fetchone()
        if not record:
            return {"success": False, "msg": "记录不存在或已处理"}
        # 删除该打卡记录（驳回）；链上由前端先调用 rejectClockIn 墓碑，保证不被自动计薪
        log_audit("CLOCK_IN_REJECTED", "", f"clock_in#{data.clock_in_id}", f"驳回工时: {record[2]}分钟")
        conn.execute(text("DELETE FROM clock_ins WHERE id=:c"), {"c": data.clock_in_id})
        conn.commit()
        return {"success": True, "msg": f"已驳回{record[2]}分钟工时记录"}

# ======================== 薪酬结算 API ========================

@app.post("/api/settle_salary")
def settle_salary(data: SettleSalaryData):
    with engine.connect() as conn:
        emp = conn.execute(text(
            "SELECT id, student_address, total_minutes FROM employments WHERE id=:e AND status='APPROVED'"
        ), {"e": data.employment_id}).fetchone()
        if not emp:
            return {"success": False, "msg": "任职合约不存在或未审批"}
        if emp[2] <= 0:
            return {"success": False, "msg": "没有已确认的工时"}

        # 获取时薪
        job = conn.execute(text("""
            SELECT j.hourly_rate_wei FROM jobs j
            JOIN employments e ON e.job_id = j.id
            WHERE e.id = :e
        """), {"e": data.employment_id}).fetchone()

        hourly_rate = int(job[0]) if job[0] else 0
        amount = (emp[2] * hourly_rate) // 60  # 分钟转小时

        conn.execute(text("""
            INSERT INTO settlements (employment_id, student_address, amount_wei, hours_settled_minutes, tx_hash, status)
            VALUES (:e, :s, :a, :h, :tx, 'CONFIRMED')
        """), {"e": data.employment_id, "s": emp[1], "a": str(amount), "h": emp[2], "tx": data.tx_hash})

        # 重置工时
        conn.execute(text("UPDATE employments SET total_minutes=0 WHERE id=:e"), {"e": data.employment_id})
        conn.commit()
        log_audit("SETTLEMENT", emp[1], f"employment#{data.employment_id}", f"结算金额: {Web3.from_wei(amount,'ether')} ETH, 工时: {emp[2]}分钟", data.tx_hash)
        return {"success": True, "msg": "薪酬结算成功", "amount_wei": str(amount), "amount_ether": str(Web3.from_wei(amount, 'ether'))}

@app.get("/api/settlements/{wallet_address}")
def get_settlements(wallet_address: str):
    with engine.connect() as conn:
        results = conn.execute(text("""
            SELECT s.id, s.amount_wei, s.hours_settled_minutes, s.tx_hash, s.status, s.settled_at,
                   j.title, j.department
            FROM settlements s
            JOIN employments e ON s.employment_id = e.id
            JOIN jobs j ON e.job_id = j.id
            WHERE s.student_address = :w
            ORDER BY s.settled_at DESC
        """), {"w": wallet_address}).fetchall()
        return [{
            "id": r[0],
            "amount_ether": str(Web3.from_wei(int(r[1]), 'ether')),
            "hours": round(r[2] / 60, 1),
            "tx_hash": r[3],
            "status": r[4],
            "settled_at": str(r[5]),
            "job_title": r[6],
            "department": r[7]
        } for r in results]

# ======================== 仪表盘 API ========================

@app.get("/api/my_dashboard/{wallet_address}")
def get_dashboard(wallet_address: str):
    with engine.connect() as conn:
        # 进行中的合约
        active = conn.execute(text("""
            SELECT e.id, j.title, j.hourly_rate_wei, e.total_minutes, e.status
            FROM employments e
            JOIN jobs j ON e.job_id = j.id
            WHERE e.student_address = :w AND e.status IN ('PENDING', 'APPROVED')
        """), {"w": wallet_address}).fetchall()

        # 已结算总额
        total = conn.execute(text(
            "SELECT COALESCE(SUM(CAST(amount_wei AS UNSIGNED)), 0) FROM settlements WHERE student_address=:w AND status='CONFIRMED'"
        ), {"w": wallet_address}).scalar()

        # 打卡次数
        clock_count = conn.execute(text(
            "SELECT COUNT(*) FROM clock_ins WHERE student_address=:w"
        ), {"w": wallet_address}).scalar()

        return {
            "active_list": [{
                "employment_id": r[0], "title": r[1],
                "hourly_rate": str(Web3.from_wei(int(r[2]), 'ether')) if r[2] and r[2] != '0' else '0',
                "total_minutes": r[3], "status": r[4]
            } for r in active],
            "total_earnings": str(Web3.from_wei(int(total), 'ether')),
            "clock_count": clock_count
        }

# ======================== 教师仪表盘 API ========================

@app.get("/api/teacher_dashboard/{wallet_address}")
def get_teacher_dashboard(wallet_address: str):
    try:
        with engine.connect() as conn:
            my_jobs = conn.execute(text("""
                SELECT id, title, COALESCE(department,''), salary_wei, status
                FROM jobs WHERE employer_address = :w
                ORDER BY created_at DESC
            """), {"w": wallet_address}).fetchall()

            pending_count = conn.execute(text(
                "SELECT COUNT(*) FROM employments WHERE employer_address=:w AND status='PENDING'"
            ), {"w": wallet_address}).scalar() or 0

            unconfirmed = 0
            try:
                unconfirmed = conn.execute(text("""
                    SELECT COUNT(*) FROM clock_ins c
                    JOIN employments e ON c.employment_id = e.id
                    WHERE e.employer_address = :w AND c.confirmed = FALSE AND c.clock_out_time IS NOT NULL
                """), {"w": wallet_address}).scalar() or 0
            except Exception:
                pass

            return {
                "my_jobs": [{
                    "id": r[0], "title": r[1], "department": r[2],
                    "salary": str(Web3.from_wei(int(r[3]), 'ether')),
                    "status": r[4]
                } for r in my_jobs],
                "pending_count": int(pending_count),
                "unconfirmed_hours": int(unconfirmed)
            }
    except Exception as e:
        return {"my_jobs": [], "pending_count": 0, "unconfirmed_hours": 0}

# ======================== 管理员 API ========================

@app.get("/api/admin/stats")
def get_admin_stats():
    try:
        with engine.connect() as conn:
            user_count = conn.execute(text("SELECT COUNT(*) FROM users")).scalar() or 0
            job_count = conn.execute(text("SELECT COUNT(*) FROM jobs")).scalar() or 0
            employment_count = conn.execute(text("SELECT COUNT(*) FROM employments")).scalar() or 0
            settlement_count = conn.execute(text("SELECT COUNT(*) FROM settlements")).scalar() or 0
            total_paid = conn.execute(text(
                "SELECT COALESCE(SUM(CAST(amount_wei AS UNSIGNED)), 0) FROM settlements WHERE status='CONFIRMED'"
            )).scalar() or 0

            return {
                "user_count": int(user_count),
                "job_count": int(job_count),
                "employment_count": int(employment_count),
                "settlement_count": int(settlement_count),
                "total_paid_ether": str(Web3.from_wei(int(total_paid), 'ether'))
            }
    except Exception as e:
        return {"user_count": 0, "job_count": 0, "employment_count": 0, "settlement_count": 0, "total_paid_ether": "0"}

@app.get("/api/admin/users")
def get_admin_users():
    try:
        with engine.connect() as conn:
            results = conn.execute(text(
                "SELECT id, username, role, COALESCE(nickname,''), COALESCE(wallet_address,''), COALESCE(created_at, NOW()) FROM users ORDER BY id"
            )).fetchall()
            return [{
                "id": r[0], "username": r[1], "role": r[2],
                "nickname": r[3], "wallet_address": r[4], "created_at": str(r[5])
            } for r in results]
    except Exception as e:
        return []

@app.post("/api/admin/update_user_role")
def update_user_role(data: AdminUserUpdate):
    with engine.connect() as conn:
        conn.execute(text("UPDATE users SET role=:r WHERE id=:i"), {"r": data.role, "i": data.user_id})
        conn.commit()
        return {"success": True, "msg": "角色更新成功"}

@app.get("/api/admin/jobs")
def admin_get_jobs():
    try:
        with engine.connect() as conn:
            results = conn.execute(text("""
                SELECT j.id, COALESCE(j.chain_job_id,0), j.title, COALESCE(j.department,''),
                       j.employer_address, j.status, COALESCE(j.salary_wei,'0')
                FROM jobs j ORDER BY j.id DESC
            """)).fetchall()
            return [{"id":r[0],"chain_job_id":r[1],"title":r[2],"department":r[3],
                     "employer":r[4],"status":r[5],"salary":str(Web3.from_wei(int(r[6]),'ether'))} for r in results]
    except Exception:
        return []

@app.get("/api/admin/employments")
def admin_get_employments():
    try:
        with engine.connect() as conn:
            results = conn.execute(text("""
                SELECT e.id, j.title, e.student_address, e.employer_address, e.status, COALESCE(e.total_minutes,0)
                FROM employments e JOIN jobs j ON e.job_id = j.id ORDER BY e.id DESC
            """)).fetchall()
            return [{"id":r[0],"job_title":r[1],"student":r[2],"employer":r[3],"status":r[4],"minutes":r[5]} for r in results]
    except Exception:
        return []

@app.post("/api/admin/delete_all_jobs")
def admin_delete_all_jobs():
    with engine.connect() as conn:
        conn.execute(text("DELETE FROM settlements"))
        conn.execute(text("DELETE FROM clock_ins"))
        conn.execute(text("DELETE FROM employments"))
        conn.execute(text("DELETE FROM jobs"))
        conn.commit()
        return {"success": True, "msg": "所有岗位已清空"}

@app.post("/api/admin/delete_user")
def admin_delete_user(data: AdminDeleteUser):
    with engine.connect() as conn:
        user = conn.execute(text("SELECT id, username, role FROM users WHERE id=:i"), {"i": data.user_id}).fetchone()
        if not user:
            return {"success": False, "msg": "用户不存在"}
        if user[2] == 'ADMIN':
            return {"success": False, "msg": "不能删除管理员账号"}
        # 删除用户关联数据
        conn.execute(text("""
            DELETE ci FROM clock_ins ci
            JOIN employments e ON ci.employment_id = e.id
            WHERE e.student_address IN (SELECT wallet_address FROM users WHERE id=:i)
        """), {"i": data.user_id})
        conn.execute(text("""
            DELETE s FROM settlements s
            JOIN employments e ON s.employment_id = e.id
            WHERE e.student_address IN (SELECT wallet_address FROM users WHERE id=:i)
        """), {"i": data.user_id})
        conn.execute(text("""
            DELETE FROM employments WHERE student_address IN (SELECT wallet_address FROM users WHERE id=:i)
        """), {"i": data.user_id})
        conn.execute(text("DELETE FROM users WHERE id=:i"), {"i": data.user_id})
        conn.commit()
        return {"success": True, "msg": f"用户 [{user[1]}] 已删除"}

@app.get("/api/admin/charts_data")
def admin_charts_data():
    """管理员可视化图表数据"""
    with engine.connect() as conn:
        # 1. 用户角色分布（饼图）
        roles = conn.execute(text(
            "SELECT role, COUNT(*) FROM users GROUP BY role"
        )).fetchall()
        user_roles = {r[0]: int(r[1]) for r in roles}

        # 2. 岗位数量按部门分布（柱状图）
        depts = conn.execute(text(
            "SELECT COALESCE(NULLIF(department,''),'未指定'), COUNT(*) FROM jobs GROUP BY COALESCE(NULLIF(department,''),'未指定') ORDER BY COUNT(*) DESC"
        )).fetchall()
        jobs_by_dept = [{"dept": d[0], "count": int(d[1])} for d in depts]

        # 3. 近7天打卡趋势（折线图）
        daily = conn.execute(text(
            "SELECT DATE(clock_in_time), COUNT(*) FROM clock_ins "
            "WHERE clock_in_time >= DATE_SUB(NOW(), INTERVAL 7 DAY) "
            "GROUP BY DATE(clock_in_time) ORDER BY DATE(clock_in_time)"
        )).fetchall()
        daily_clock_ins = [{"date": str(r[0]), "count": int(r[1])} for r in daily]

        # 4. 任职状态分布（辅助饼图）
        emp_status = conn.execute(text(
            "SELECT status, COUNT(*) FROM employments GROUP BY status"
        )).fetchall()
        employment_status = {r[0]: int(r[1]) for r in emp_status}

        # 5. 近7天结算趋势（辅助折线图）
        settles = conn.execute(text(
            "SELECT DATE(settled_at), COUNT(*), COALESCE(SUM(CAST(amount_wei AS UNSIGNED)),0) "
            "FROM settlements WHERE settled_at >= DATE_SUB(NOW(), INTERVAL 7 DAY) "
            "GROUP BY DATE(settled_at) ORDER BY DATE(settled_at)"
        )).fetchall()
        daily_settlements = [{
            "date": str(r[0]), "count": int(r[1]),
            "amount_eth": str(Web3.from_wei(int(r[2]), 'ether'))
        } for r in settles]

        return {
            "user_roles": user_roles,
            "jobs_by_dept": jobs_by_dept,
            "daily_clock_ins": daily_clock_ins,
            "employment_status": employment_status,
            "daily_settlements": daily_settlements
        }

@app.get("/api/admin/system_info")
def admin_system_info():
    with engine.connect() as conn:
        user_count = int(conn.execute(text("SELECT COUNT(*) FROM users")).scalar() or 0)
        teacher_count = int(conn.execute(text("SELECT COUNT(*) FROM users WHERE role='TEACHER'")).scalar() or 0)
        student_count = int(conn.execute(text("SELECT COUNT(*) FROM users WHERE role='STUDENT'")).scalar() or 0)
        job_count = int(conn.execute(text("SELECT COUNT(*) FROM jobs")).scalar() or 0)
        active_jobs = int(conn.execute(text("SELECT COUNT(*) FROM jobs WHERE status IN ('PUBLISHED','APPROVED')")).scalar() or 0)
        emp_count = int(conn.execute(text("SELECT COUNT(*) FROM employments")).scalar() or 0)
        approved_emps = int(conn.execute(text("SELECT COUNT(*) FROM employments WHERE status='APPROVED'")).scalar() or 0)
        total_hours = int(conn.execute(text("SELECT COALESCE(SUM(total_minutes),0) FROM employments WHERE status='APPROVED'")).scalar() or 0)
        settle_count = int(conn.execute(text("SELECT COUNT(*) FROM settlements")).scalar() or 0)
        total_paid = int(conn.execute(text("SELECT COALESCE(SUM(CAST(amount_wei AS UNSIGNED)),0) FROM settlements WHERE status='CONFIRMED'")).scalar() or 0)
        contract_balance = 0
        try:
            from web3 import Web3 as W3
            w3 = W3(W3.HTTPProvider('http://127.0.0.1:8545'))
            contract_balance = int(w3.eth.get_balance(W3.to_checksum_address('0x5FbDB2315678afecb367f032d93F642f64180aa3')))
        except Exception:
            pass
        return {
            "users": {"total": user_count, "teachers": teacher_count, "students": student_count},
            "jobs": {"total": job_count, "active": active_jobs},
            "employments": {"total": emp_count, "active": approved_emps, "total_hours_minutes": total_hours},
            "settlements": {"count": settle_count, "total_paid_ether": str(Web3.from_wei(total_paid, 'ether'))},
            "contract": {"balance_ether": str(Web3.from_wei(contract_balance, 'ether'))},
            "rpc_url": "http://127.0.0.1:8545",
            "chain_id": 887766
        }

# ======================== 自动结算管理 API ========================

@app.get("/api/settlement_config")
def get_settlement_config():
    """获取结算配置"""
    return {
        "auto_enabled": get_config("auto_settlement_enabled", "true") == "true",
        "cycle_days": int(get_config("settlement_cycle_days", "30")),
        "grace_period_hours": int(get_config("grace_period_hours", "48")),
        "contract_address": get_contract_address()
    }

class SettlementConfigUpdate(BaseModel):
    auto_enabled: bool = True
    cycle_days: int = 30
    grace_period_hours: int = 48

@app.post("/api/settlement_config")
def update_settlement_config(data: SettlementConfigUpdate):
    """更新结算配置"""
    set_config("auto_settlement_enabled", "true" if data.auto_enabled else "false")
    set_config("settlement_cycle_days", str(data.cycle_days))
    set_config("grace_period_hours", str(data.grace_period_hours))

    # 同步到链上合约
    receipt, err = send_contract_tx("updateSettlementConfig", [data.cycle_days, data.auto_enabled, data.grace_period_hours])
    if receipt and receipt.status == 1:
        log_audit("CONFIG_UPDATED", "admin", "settlement_config", f"周期:{data.cycle_days}天, 自动:{data.auto_enabled}, 宽限:{data.grace_period_hours}小时", receipt.transactionHash.hex())
        return {"success": True, "msg": "配置已更新并同步到链上"}
    else:
        log_audit("CONFIG_UPDATED", "admin", "settlement_config", f"周期:{data.cycle_days}天, 自动:{data.auto_enabled} (链上同步失败: {err})")
        return {"success": True, "msg": f"配置已更新（链上同步失败: {err}）"}

@app.get("/api/auto_settlement_status")
def get_auto_settlement_status():
    """获取自动结算状态"""
    is_due = call_contract_function("isSettlementDue")
    config = call_contract_function("getSettlementConfig")
    pending_ids = call_contract_function("getPendingSettlementIds")

    next_settle_ts = config[4] if config else 0
    next_settle_str = datetime.fromtimestamp(next_settle_ts).strftime("%Y-%m-%d %H:%M") if next_settle_ts > 0 else "未知"
    last_settle_ts = config[1] if config else 0
    last_settle_str = datetime.fromtimestamp(last_settle_ts).strftime("%Y-%m-%d %H:%M") if last_settle_ts > 0 else "从未"

    pending_count = len(pending_ids) if pending_ids else 0
    total_pending_wei = 0
    if pending_ids:
        with engine.connect() as conn:
            for emp_id in pending_ids:
                try:
                    emp = call_contract_function("getEmployment", [emp_id])
                    if emp and emp[4] > 0:
                        r = conn.execute(text("""
                            SELECT j.hourly_rate_wei FROM jobs j
                            JOIN employments e ON e.job_id = j.id
                            WHERE e.chain_employment_id = :c
                        """), {"c": emp_id}).fetchone()
                        if r and r[0]:
                            total_pending_wei += (emp[4] * int(r[0])) // 60
                except Exception:
                    pass

    return {
        "is_due": bool(is_due),
        "auto_enabled": config[2] if config else False,
        "cycle_days": config[0] if config else 30,
        "grace_period_hours": config[3] if config else 48,
        "last_settlement": last_settle_str,
        "next_settlement": next_settle_str,
        "pending_count": pending_count,
        "pending_amount_ether": str(Web3.from_wei(total_pending_wei, 'ether'))
    }

@app.post("/api/trigger_auto_settlement")
def trigger_auto_settlement():
    """手动触发自动结算"""
    log_audit("MANUAL_SETTLEMENT_TRIGGER", "admin", "", "管理员手动触发结算")
    receipt, err = send_contract_tx("executeAutoSettlement")
    if receipt and receipt.status == 1:
        _sync_settlements_from_chain()
        log_audit("MANUAL_SETTLEMENT_SUCCESS", "admin", "", f"手动结算成功 tx: {receipt.transactionHash.hex()}", receipt.transactionHash.hex())
        return {"success": True, "msg": "自动结算执行成功", "tx_hash": receipt.transactionHash.hex()}
    else:
        log_audit("MANUAL_SETTLEMENT_FAILED", "admin", "", f"手动结算失败: {err}")
        return {"success": False, "msg": f"结算失败: {err}"}

@app.get("/api/audit_logs")
def get_audit_logs(limit: int = 50):
    """获取审计日志"""
    try:
        with engine.connect() as conn:
            results = conn.execute(text("""
                SELECT id, action, actor, target, detail, tx_hash, created_at
                FROM audit_logs ORDER BY id DESC LIMIT :l
            """), {"l": limit}).fetchall()
            return [{
                "id": r[0], "action": r[1], "actor": r[2], "target": r[3],
                "detail": r[4], "tx_hash": r[5], "created_at": str(r[6])
            } for r in results]
    except Exception as e:
        return []

@app.post("/api/repair_chain_ids")
def repair_chain_ids():
    """手动修复链上岗位ID映射"""
    repair_chain_job_ids()
    return {"success": True, "msg": "修复完成"}

# ======================== 静态文件挂载 (必须放最后) ========================

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
