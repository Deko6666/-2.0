import os
import shutil
import time
import threading
import json
import httpx
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, UploadFile, File, Request
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

        # ==================== 毕设进阶功能：新表 ====================

        # 功能一：学生画像表（精准资助撮合）
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS student_profiles (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                wallet_address VARCHAR(42) NOT NULL,
                financial_level ENUM('A','B','C','NORMAL') DEFAULT 'NORMAL',
                available_hours_per_week INT DEFAULT 10,
                reputation_score INT DEFAULT 80,
                monthly_aid_received DECIMAL(10,2) DEFAULT 0,
                certification_hash VARCHAR(130),
                verified BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY (user_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))

        # 功能一：修补users表（困难等级、信誉分）
        for col, col_def in [
            ("financial_level", "ENUM('A','B','C','NORMAL') DEFAULT 'NORMAL'"),
            ("reputation_score", "INT DEFAULT 80"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col} {col_def}"))
            except Exception:
                pass

        # 功能二：工时风控 — clock_ins表扩展IP和风险标记
        for col, col_def in [
            ("clock_in_ip", "VARCHAR(45)"),
            ("clock_out_ip", "VARCHAR(45)"),
            ("risk_flag", "VARCHAR(50) DEFAULT NULL"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE clock_ins ADD COLUMN {col} {col_def}"))
            except Exception:
                pass

        # 功能四：争议申诉 — clock_ins表扩展驳回标记（软删除，保留记录供申诉）
        for col, col_def in [
            ("rejected", "BOOLEAN DEFAULT FALSE"),
            ("original_work_minutes", "INT DEFAULT 0"),
        ]:
            try:
                conn.execute(text(f"ALTER TABLE clock_ins ADD COLUMN {col} {col_def}"))
            except Exception:
                pass

        # 功能二：工时统计缓存表（每行=一个学生的一周统计，月工时由周记录汇总计算）
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS hour_limits (
                id INT AUTO_INCREMENT PRIMARY KEY,
                student_address VARCHAR(42) NOT NULL,
                week_start DATE NOT NULL,
                month_start DATE NOT NULL,
                weekly_minutes INT DEFAULT 0,
                monthly_minutes INT DEFAULT 0,
                UNIQUE KEY idx_week (student_address, week_start)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))
        # 修复已有数据：移除旧的 idx_month 唯一键（若存在），防止同月多周插入冲突
        try:
            conn.execute(text("ALTER TABLE hour_limits DROP INDEX idx_month"))
        except Exception:
            pass

        # 功能四：争议申诉表
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS disputes (
                id INT AUTO_INCREMENT PRIMARY KEY,
                clock_in_id INT NOT NULL,
                employment_id INT NOT NULL,
                student_address VARCHAR(42) NOT NULL,
                employer_address VARCHAR(42) NOT NULL,
                reason TEXT NOT NULL,
                evidence_hash VARCHAR(130),
                status ENUM('PENDING','IN_DISPUTE','RESOLVED_UPHELD','RESOLVED_OVERTURNED') DEFAULT 'PENDING',
                admin_decision TEXT,
                admin_address VARCHAR(42),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                resolved_at TIMESTAMP NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))

        # 岗位图片表
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS job_images (
                id INT AUTO_INCREMENT PRIMARY KEY,
                job_id INT NOT NULL,
                image_url VARCHAR(500) NOT NULL,
                uploaded_by VARCHAR(42),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """))

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

def get_sbt_contract_address() -> str:
    """读取 SBT 勋章合约地址"""
    try:
        if os.path.exists("sbt_address.txt"):
            with open("sbt_address.txt", "r") as f:
                addr = f.read().strip()
                if addr and addr.startswith("0x"):
                    return addr
    except Exception:
        pass
    return "0x0000000000000000000000000000000000000000"

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
    {"inputs":[{"type":"uint256"}],"name":"getClockIn","outputs":[{"type":"tuple","components":[{"type":"uint256"},{"type":"uint256"},{"type":"address"},{"type":"uint256"},{"type":"uint256"},{"type":"uint256"},{"type":"bool"}]}],"stateMutability":"view","type":"function"},
    {"inputs":[{"type":"uint256"},{"type":"uint256"}],"name":"adminConfirmClockIn","outputs":[],"stateMutability":"nonpayable","type":"function"}
]''')

SBT_ABI = json.loads('''[
    {"inputs":[{"type":"address"}],"name":"getStudentBadges","outputs":[{"type":"uint256[]"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"type":"uint256"}],"name":"getBadge","outputs":[{"type":"tuple","components":[{"type":"uint256"},{"type":"address"},{"type":"string"},{"type":"string"},{"type":"uint256"},{"type":"string"},{"type":"uint8"},{"type":"uint256"},{"type":"bool"}]}],"stateMutability":"view","type":"function"},
    {"inputs":[{"type":"address"}],"name":"canIssue","outputs":[{"type":"bool"}],"stateMutability":"view","type":"function"}
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

def call_sbt_function(fn_name: str, args: list = None):
    """调用 SBT 勋章合约视图函数"""
    try:
        w3 = get_web3()
        addr = get_sbt_contract_address()
        if not addr or addr == "0x0000000000000000000000000000000000000000":
            return None
        contract = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=SBT_ABI)
        fn = getattr(contract.functions, fn_name)
        return fn(*(args or [])).call()
    except Exception as e:
        print(f"[SBT VIEW ERROR] {fn_name}: {e}")
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
def clock_in(data: ClockInData, request: Request):
    client_ip = get_client_ip(request)

    # 工时红线检查
    hour_status = check_hour_limits(data.student)
    if hour_status.get("weekly_exceeded"):
        return {"success": False, "msg": "本周工时已达教育部规定上限（8小时），无法继续打卡，请合理安排学业"}
    if hour_status.get("monthly_exceeded"):
        return {"success": False, "msg": "本月工时已达教育部规定上限（40小时），无法继续打卡，请合理安排学业"}

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
            INSERT INTO clock_ins (employment_id, student_address, clock_in_time, chain_clock_in_id, clock_in_ip)
            VALUES (:e, :s, NOW(), :ccid, :ip)
        """), {"e": data.employment_id, "s": data.student, "ccid": data.chain_clock_in_id, "ip": client_ip})
        conn.commit()

        # 获取刚插入的记录ID和上班时间（供前端启动计时器）
        record = conn.execute(text(
            "SELECT id, clock_in_time FROM clock_ins WHERE employment_id=:e ORDER BY id DESC LIMIT 1"
        ), {"e": data.employment_id}).fetchone()

        return {"success": True, "clock_in_id": record[0], "clock_in_time": str(record[1]), "msg": "上班打卡成功"}

@app.post("/api/clock_out")
def clock_out(data: ClockOutData, request: Request):
    client_ip = get_client_ip(request)

    with engine.connect() as conn:
        record = conn.execute(text(
            "SELECT id, chain_clock_in_id, clock_out_time, clock_in_ip, student_address, employment_id FROM clock_ins WHERE id=:c"
        ), {"c": data.clock_in_id}).fetchone()
        if not record:
            return {"success": False, "msg": "打卡记录不存在"}
        if record[2] is not None:
            return {"success": False, "msg": "已经下班打卡过了"}

        # 工时由服务端按时间戳自动计算，不再信任客户端传值（防虚报）
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

        # 风险检测：IP不匹配
        risk_flag = detect_clock_risk(record[4], client_ip, record[3])

        conn.execute(text("""
            UPDATE clock_ins SET clock_out_time=NOW(), work_minutes=:m, clock_out_ip=:ip, risk_flag=:rf WHERE id=:c
        """), {"m": work_minutes, "ip": client_ip, "rf": risk_flag, "c": data.clock_in_id})
        conn.commit()

        # 更新工时配额统计
        if work_minutes > 0:
            update_hour_limits(record[4], work_minutes)

        msg = f"下班打卡成功，本次工时{work_minutes}分钟（系统自动计时）"
        if risk_flag == "IP_MISMATCH":
            msg += " ⚠️ 检测到上班/下班IP不匹配，已标记为异常打卡，等待教师人工复核"
        return {"success": True, "work_minutes": work_minutes, "risk_flag": risk_flag, "msg": msg}

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
        # 软删除：标记为驳回（保留记录供争议申诉使用），work_minutes 清零避免计入工时
        # original_work_minutes 保存原值，供管理员裁决"强制通过"时恢复
        conn.execute(text(
            "UPDATE clock_ins SET confirmed=TRUE, rejected=TRUE, original_work_minutes=work_minutes, work_minutes=0 WHERE id=:c"
        ), {"c": data.clock_in_id})
        log_audit("CLOCK_IN_REJECTED", "", f"clock_in#{data.clock_in_id}", f"驳回工时: {record[2]}分钟")
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

# ======================== 功能一：精准资助 — 人岗智能撮合算法 ========================

def calculate_match_score(profile: dict, job_info: dict) -> dict:
    """
    多属性加权TOPSIS算法计算人岗匹配得分
    指标：困难等级(w1=0.35) + 课余时间匹配度(w2=0.25) + 信誉分(w3=0.25) - 本月已获助学金(w4=0.15)
    论文第4章核心算法
    """
    financial_weights = {'A': 1.0, 'B': 0.7, 'C': 0.4, 'NORMAL': 0.1}
    w = [0.35, 0.25, 0.25, 0.15]

    # 1. 困难等级得分（正向）
    f1 = financial_weights.get(profile.get('financial_level', 'NORMAL'), 0.1)

    # 2. 课余时间匹配度（正向）= min(可用工时/岗位需求工时, 1.0)
    job_hours = job_info.get('estimated_hours_per_week', 10)
    available = profile.get('available_hours_per_week', 10)
    f2 = min(available / max(job_hours, 1), 1.0)

    # 3. 信誉分归一化（正向）
    f3 = profile.get('reputation_score', 80) / 100.0

    # 4. 本月已获助学金（负向，越低越好）
    max_aid = 2000.0
    f4 = min(float(profile.get('monthly_aid_received', 0)) / max_aid, 1.0)

    # 加权得分
    score = w[0]*f1 + w[1]*f2 + w[2]*f3 + w[3]*(1 - f4)

    # 推荐理由
    reasons = []
    if f1 >= 0.7: reasons.append("困难等级高")
    if f2 >= 0.8: reasons.append("课余时间充裕")
    if f3 >= 0.8: reasons.append("历史信誉良好")
    if f4 <= 0.3: reasons.append("本月助学金额度充足")
    if not reasons: reasons.append("综合条件适中")

    return {
        "score": round(score * 100, 1),
        "financial_score": round(f1 * 100, 1),
        "time_score": round(f2 * 100, 1),
        "reputation_score_val": round(f3 * 100, 1),
        "aid_score": round((1 - f4) * 100, 1),
        "reason": "、".join(reasons)
    }

class StudentProfileUpdate(BaseModel):
    wallet_address: str
    financial_level: str = "NORMAL"
    available_hours_per_week: int = 10
    certification_hash: str = ""

@app.post("/api/student_profile")
def save_student_profile(data: StudentProfileUpdate):
    """学生提交/更新画像"""
    with engine.connect() as conn:
        user = conn.execute(text(
            "SELECT id FROM users WHERE wallet_address=:w"
        ), {"w": data.wallet_address}).fetchone()
        if not user:
            return {"success": False, "msg": "用户不存在"}

        conn.execute(text("""
            INSERT INTO student_profiles (user_id, wallet_address, financial_level, available_hours_per_week, certification_hash)
            VALUES (:uid, :wl, :fl, :ah, :ch)
            ON DUPLICATE KEY UPDATE financial_level=:fl, available_hours_per_week=:ah, certification_hash=:ch, verified=FALSE
        """), {"uid": user[0], "wl": data.wallet_address, "fl": data.financial_level,
               "ah": data.available_hours_per_week, "ch": data.certification_hash})
        conn.execute(text(
            "UPDATE users SET financial_level=:fl WHERE id=:uid"
        ), {"fl": data.financial_level, "uid": user[0]})
        conn.commit()
        log_audit("PROFILE_UPDATE", data.wallet_address, "", f"困难等级:{data.financial_level}")
        return {"success": True, "msg": "画像已更新，等待资助中心审核"}

@app.get("/api/student_profile/{wallet}")
def get_student_profile(wallet: str):
    """获取学生画像"""
    with engine.connect() as conn:
        row = conn.execute(text("""
            SELECT p.financial_level, p.available_hours_per_week, p.reputation_score,
                   COALESCE(p.monthly_aid_received,0), p.certification_hash, p.verified,
                   COALESCE(u.nickname,'')
            FROM student_profiles p JOIN users u ON p.user_id = u.id
            WHERE p.wallet_address=:w
        """), {"w": wallet}).fetchone()
        if not row:
            return {"financial_level": "NORMAL", "available_hours_per_week": 10,
                    "reputation_score": 80, "monthly_aid_received": 0, "verified": False}
        return {
            "financial_level": row[0], "available_hours_per_week": row[1],
            "reputation_score": row[2], "monthly_aid_received": float(row[3]),
            "certification_hash": row[4], "verified": bool(row[5]), "nickname": row[6]
        }

@app.get("/api/admin/verify_profiles")
def admin_get_verify_profiles():
    """管理员获取待审核的学生画像"""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT p.id, p.wallet_address, p.financial_level, p.available_hours_per_week,
                   p.certification_hash, COALESCE(u.nickname,''), p.created_at
            FROM student_profiles p JOIN users u ON p.user_id = u.id
            WHERE p.verified = FALSE ORDER BY p.created_at DESC
        """)).fetchall()
        return [{"id": r[0], "wallet": r[1], "financial_level": r[2],
                 "available_hours": r[3], "cert_hash": r[4] or "",
                 "nickname": r[5], "created_at": str(r[6])} for r in rows]

class VerifyProfile(BaseModel):
    profile_id: int
    approved: bool
    reputation_score: int = 80
    monthly_aid: float = 0

@app.post("/api/admin/verify_profile")
def admin_verify_profile(data: VerifyProfile):
    """资助中心审核学生画像"""
    with engine.connect() as conn:
        if data.approved:
            conn.execute(text("""
                UPDATE student_profiles SET verified=TRUE, reputation_score=:rs, monthly_aid_received=:ma WHERE id=:pid
            """), {"rs": data.reputation_score, "ma": data.monthly_aid, "pid": data.profile_id})
            conn.commit()
            log_audit("PROFILE_VERIFIED", "admin", f"profile#{data.profile_id}", f"信誉分:{data.reputation_score}")
            return {"success": True, "msg": "审核通过"}
        else:
            conn.execute(text("DELETE FROM student_profiles WHERE id=:pid"), {"pid": data.profile_id})
            conn.commit()
            return {"success": True, "msg": "已驳回"}

@app.get("/api/match_score/{employment_id}")
def get_match_score(employment_id: int):
    """获取某申请的算法匹配详情（教师审批参考）"""
    with engine.connect() as conn:
        emp = conn.execute(text("""
            SELECT e.student_address, j.title, COALESCE(j.department,''), e.job_id
            FROM employments e JOIN jobs j ON e.job_id = j.id WHERE e.id=:eid
        """), {"eid": employment_id}).fetchone()
        if not emp:
            return {"score": 0, "reason": "记录不存在"}

        profile_row = conn.execute(text("""
            SELECT financial_level, available_hours_per_week, reputation_score, monthly_aid_received
            FROM student_profiles WHERE wallet_address=:w
        """), {"w": emp[0]}).fetchone()

        profile = {
            "financial_level": profile_row[0] if profile_row else "NORMAL",
            "available_hours_per_week": profile_row[1] if profile_row else 10,
            "reputation_score": profile_row[2] if profile_row else 80,
            "monthly_aid_received": float(profile_row[3]) if profile_row else 0,
        }
        return calculate_match_score(profile, {"estimated_hours_per_week": 10})

# ======================== 功能二：规范风控 — 工时红线与防代刷 ========================

def get_client_ip(request):
    """获取客户端真实IP"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"

def update_hour_limits(student_address: str, minutes: int):
    """更新学生周/月工时统计"""
    from datetime import date, timedelta
    today = date.today()
    week_start = today - timedelta(days=today.weekday())  # 本周一
    month_start = today.replace(day=1)  # 本月1日

    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO hour_limits (student_address, week_start, month_start, weekly_minutes, monthly_minutes)
            VALUES (:sa, :ws, :ms, :m, :m)
            ON DUPLICATE KEY UPDATE weekly_minutes = weekly_minutes + :m, monthly_minutes = monthly_minutes + :m
        """), {"sa": student_address, "ws": str(week_start), "ms": str(month_start), "m": minutes})
        conn.commit()

def check_hour_limits(student_address: str) -> dict:
    """检查学生是否超出工时红线"""
    from datetime import date, timedelta
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)

    MAX_WEEKLY = 480   # 8小时
    MAX_MONTHLY = 2400  # 40小时

    with engine.connect() as conn:
        # 本周工时：直接读当前周记录
        row = conn.execute(text("""
            SELECT COALESCE(weekly_minutes,0)
            FROM hour_limits WHERE student_address=:sa AND week_start=:ws
        """), {"sa": student_address, "ws": str(week_start)}).fetchone()
        weekly = row[0] if row else 0

        # 本月工时：汇总本月所有周记录的 weekly_minutes（不再依赖 idx_month 唯一键）
        month_row = conn.execute(text("""
            SELECT COALESCE(SUM(weekly_minutes),0) FROM hour_limits
            WHERE student_address=:sa AND month_start=:ms
        """), {"sa": student_address, "ms": str(month_start)}).fetchone()
        monthly = month_row[0] if month_row else 0

    return {
        "weekly_minutes": weekly,
        "monthly_minutes": monthly,
        "weekly_remaining": max(0, MAX_WEEKLY - weekly),
        "monthly_remaining": max(0, MAX_MONTHLY - monthly),
        "weekly_exceeded": weekly >= MAX_WEEKLY,
        "monthly_exceeded": monthly >= MAX_MONTHLY,
        "max_weekly": MAX_WEEKLY,
        "max_monthly": MAX_MONTHLY
    }

def detect_clock_risk(student_address: str, current_ip: str, clock_in_ip: str = None) -> str:
    """检测打卡风险"""
    if clock_in_ip and current_ip and clock_in_ip != current_ip:
        # 简单IP不匹配检测（实际应做IP归属地比对）
        in_parts = clock_in_ip.split(".")
        out_parts = current_ip.split(".")
        if len(in_parts) >= 2 and len(out_parts) >= 2:
            if in_parts[0] != out_parts[0] or in_parts[1] != out_parts[1]:
                return "IP_MISMATCH"
    return None

@app.get("/api/student_hour_status/{wallet}")
def get_student_hour_status(wallet: str):
    """学生查看本周/本月剩余工时"""
    return check_hour_limits(wallet)

@app.get("/api/risk_alerts")
def get_risk_alerts():
    """管理员查看所有风险标记记录"""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT c.id, c.student_address, c.clock_in_time, c.clock_out_time,
                   c.work_minutes, c.risk_flag, c.clock_in_ip, c.clock_out_ip,
                   COALESCE(u.nickname,''), j.title
            FROM clock_ins c
            LEFT JOIN employments e ON c.employment_id = e.id
            LEFT JOIN jobs j ON e.job_id = j.id
            LEFT JOIN users u ON c.student_address = u.wallet_address
            WHERE c.risk_flag IS NOT NULL
            ORDER BY c.clock_in_time DESC LIMIT 100
        """)).fetchall()
        return [{"id": r[0], "student": r[1], "clock_in": str(r[2]), "clock_out": str(r[3]),
                 "minutes": r[4], "risk_flag": r[5], "in_ip": r[6], "out_ip": r[7],
                 "nickname": r[8], "job_title": r[9] or ""} for r in rows]

# ======================== 功能四：权益闭环 — 争议申诉与仲裁 ========================

class DisputeCreate(BaseModel):
    clock_in_id: int
    reason: str
    evidence_hash: str = ""

@app.post("/api/disputes")
def create_dispute(data: DisputeCreate):
    """学生发起争议申诉（被驳回后72小时内）"""
    with engine.connect() as conn:
        ci = conn.execute(text("""
            SELECT c.employment_id, c.student_address, c.clock_in_time, c.work_minutes, c.confirmed, COALESCE(c.rejected, FALSE)
            FROM clock_ins c WHERE c.id=:cid
        """), {"cid": data.clock_in_id}).fetchone()
        if not ci:
            return {"success": False, "msg": "打卡记录不存在"}
        if ci[5]:  # rejected — 允许申诉（驳回记录）
            pass
        elif ci[4]:  # confirmed 且未驳回 — 已确认的正常记录，不可申诉
            return {"success": False, "msg": "该记录已确认，无法申诉"}

        emp = conn.execute(text(
            "SELECT employer_address FROM employments WHERE id=:eid"
        ), {"eid": ci[0]}).fetchone()
        if not emp:
            return {"success": False, "msg": "任职记录不存在"}

        # 检查是否已有申诉
        existing = conn.execute(text(
            "SELECT id FROM disputes WHERE clock_in_id=:cid AND status IN ('PENDING','IN_DISPUTE')"
        ), {"cid": data.clock_in_id}).fetchone()
        if existing:
            return {"success": False, "msg": "该记录已有待处理的申诉"}

        conn.execute(text("""
            INSERT INTO disputes (clock_in_id, employment_id, student_address, employer_address, reason, evidence_hash)
            VALUES (:ci, :ei, :sa, :ea, :r, :eh)
        """), {"ci": data.clock_in_id, "ei": ci[0], "sa": ci[1], "ea": emp[0],
               "r": data.reason, "eh": data.evidence_hash})
        conn.commit()
        log_audit("DISPUTE_CREATED", ci[1], f"clock_in#{data.clock_in_id}", data.reason[:100])
        return {"success": True, "msg": "申诉已提交，等待管理员裁决"}

@app.get("/api/disputes/{wallet}")
def get_student_disputes(wallet: str):
    """学生查看自己的申诉"""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT d.id, d.clock_in_id, d.reason, d.status, d.admin_decision, d.created_at, d.resolved_at,
                   c.clock_in_time, c.work_minutes, j.title
            FROM disputes d
            LEFT JOIN clock_ins c ON d.clock_in_id = c.id
            LEFT JOIN employments e ON d.employment_id = e.id
            LEFT JOIN jobs j ON e.job_id = j.id
            WHERE d.student_address=:w ORDER BY d.created_at DESC
        """), {"w": wallet}).fetchall()
        return [{"id": r[0], "clock_in_id": r[1], "reason": r[2], "status": r[3],
                 "admin_decision": r[4], "created_at": str(r[5]),
                 "resolved_at": str(r[6]) if r[6] else None,
                 "clock_in_time": str(r[7]), "work_minutes": r[8], "job_title": r[9] or ""} for r in rows]

@app.get("/api/admin/disputes")
def admin_get_disputes():
    """管理员查看所有争议"""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT d.id, d.clock_in_id, d.employment_id, d.student_address, d.employer_address,
                   d.reason, d.evidence_hash, d.status, d.created_at,
                   COALESCE(su.nickname,''), COALESCE(eu.nickname,''),
                   c.clock_in_time, c.clock_out_time, c.work_minutes, j.title
            FROM disputes d
            LEFT JOIN clock_ins c ON d.clock_in_id = c.id
            LEFT JOIN employments e ON d.employment_id = e.id
            LEFT JOIN jobs j ON e.job_id = j.id
            LEFT JOIN users su ON d.student_address = su.wallet_address
            LEFT JOIN users eu ON d.employer_address = eu.wallet_address
            ORDER BY d.created_at DESC
        """)).fetchall()
        return [{
            "id": r[0], "clock_in_id": r[1], "employment_id": r[2],
            "student": r[3], "employer": r[4], "reason": r[5],
            "evidence_hash": r[6] or "", "status": r[7], "created_at": str(r[8]),
            "student_nickname": r[9], "teacher_nickname": r[10],
            "clock_in_time": str(r[11]), "clock_out_time": str(r[12]) if r[12] else None,
            "work_minutes": r[13], "job_title": r[14] or ""
        } for r in rows]

class DisputeResolve(BaseModel):
    dispute_id: int
    uphold: bool  # True=维持驳回, False=强制通过
    decision: str = ""

@app.post("/api/admin/resolve_dispute")
def admin_resolve_dispute(data: DisputeResolve):
    """管理员裁决争议"""
    with engine.connect() as conn:
        dispute = conn.execute(text(
            "SELECT clock_in_id, employment_id, student_address, status FROM disputes WHERE id=:did"
        ), {"did": data.dispute_id}).fetchone()
        if not dispute:
            return {"success": False, "msg": "争议不存在"}
        if dispute[3] not in ('PENDING', 'IN_DISPUTE'):
            return {"success": False, "msg": "该争议已裁决"}

        new_status = "RESOLVED_UPHELD" if data.uphold else "RESOLVED_OVERTURNED"

        if not data.uphold:
            # 强制通过：恢复打卡记录，同步链上+数据库
            ci = conn.execute(text(
                "SELECT COALESCE(original_work_minutes, work_minutes, 0), employment_id, COALESCE(chain_clock_in_id, 0) FROM clock_ins WHERE id=:cid"
            ), {"cid": dispute[0]}).fetchone()
            if ci:
                work_min = ci[0]
                chain_cid = ci[2]
                # 1) 链上恢复：调用 adminConfirmClockIn 恢复被墓碑的打卡记录（绕过雇主权限）
                chain_ok = False
                if chain_cid > 0 and work_min > 0:
                    try:
                        tx_hash, err = send_contract_tx("adminConfirmClockIn", [chain_cid, work_min])
                        chain_ok = bool(tx_hash)
                        if not chain_ok:
                            print(f"[DISPUTE] 链上恢复失败 clock_in#{chain_cid}: {err}")
                    except Exception as e:
                        print(f"[DISPUTE] 链上恢复异常 clock_in#{chain_cid}: {e}")
                # 2) 数据库恢复：标记为已确认（链上已确认+工时已累加），教师无需再操作
                conn.execute(text(
                    "UPDATE clock_ins SET confirmed=TRUE, rejected=FALSE, risk_flag=NULL, work_minutes=:wm WHERE id=:cid"
                ), {"cid": dispute[0], "wm": work_min})
                if not chain_ok and chain_cid > 0:
                    # 链上恢复失败时，仍需更新 DB 工时（教师端无法再操作链上）
                    conn.execute(text(
                        "UPDATE employments SET total_minutes = total_minutes + :m WHERE id=:eid"
                    ), {"m": work_min, "eid": ci[1]})

        conn.execute(text("""
            UPDATE disputes SET status=:s, admin_decision=:d, resolved_at=NOW() WHERE id=:did
        """), {"s": new_status, "d": data.decision, "did": data.dispute_id})
        conn.commit()
        log_audit("DISPUTE_RESOLVED", "admin", f"dispute#{data.dispute_id}", f"裁决:{new_status}")
        return {"success": True, "msg": "裁决已生效" if not data.uphold else "维持驳回"}

# ======================== 功能三：SBT履约勋章 ========================

@app.post("/api/issue_badge")
def issue_badge():
    """教师颁发履约勋章（触发SBT铸造）"""
    # 此端点由前端调用，实际铸造通过MetaMask完成
    # 这里只做数据库记录
    return {"success": True, "msg": "请通过前端MetaMask完成链上勋章铸造"}

@app.get("/api/badges/{wallet}")
def get_badges(wallet: str):
    """查询某学生的勋章列表（从链上读取）"""
    try:
        badges = call_sbt_function("getStudentBadges", [wallet])
        if badges:
            result = []
            for token_id in badges:
                badge = call_sbt_function("getBadge", [token_id])
                if badge:
                    result.append({
                        "token_id": token_id, "job_title": badge[2], "department": badge[3],
                        "total_hours": badge[4], "teacher_comment": badge[5],
                        "star_rating": badge[6], "issued_at": badge[7], "revoked": badge[8]
                    })
            return result
    except Exception as e:
        print(f"[BADGE ERROR] {e}")
    return []

@app.get("/api/verify_badge/{token_id}")
def verify_badge(token_id: int):
    """公开验真接口：验证勋章真伪"""
    try:
        badge = call_sbt_function("getBadge", [token_id])
        if badge and badge[0] > 0:
            return {
                "valid": not badge[8],  # not revoked
                "student": badge[1], "job_title": badge[2], "department": badge[3],
                "total_hours": badge[4], "teacher_comment": badge[5],
                "star_rating": badge[6], "issued_at": badge[7], "revoked": badge[8]
            }
    except Exception as e:
        print(f"[VERIFY ERROR] {e}")
    return {"valid": False, "msg": "勋章不存在或验证失败"}

# ======================== AI 岗位简介生成 ========================

class AIGenerateRequest(BaseModel):
    job_title: str
    department: str = ""
    hourly_rate: str = ""

@app.post("/api/ai_generate_desc")
def ai_generate_desc(data: AIGenerateRequest):
    """调用 AI 生成岗位简介（约50字）"""
    api_key = get_config("ai_api_key", "")
    api_url = get_config("ai_api_url", "https://goldenglow.webn.cc/v1/chat/completions")
    model = get_config("ai_model", "gemini-2.5-flash")

    if not api_key:
        return {"success": False, "msg": "AI 未配置，请联系管理员在系统设置中填写 API Key"}

    prompt = f"""你是一个大学勤工俭学岗位描述生成器。
请为以下岗位生成一段简洁的岗位简介，约50字，语言正式、适合校园公告。

岗位名称：{data.job_title}
所属部门：{data.department or '未指定'}
时薪：{data.hourly_rate or '面议'} ETH

要求：
1. 介绍岗位主要工作内容
2. 说明工作时间安排
3. 语言简洁专业
4. 只输出简介内容，不要标题和多余文字"""

    try:
        # 确保 URL 以 /v1/chat/completions 结尾
        if not api_url.endswith('/v1/chat/completions'):
            api_url = api_url.rstrip('/') + '/v1/chat/completions'

        with httpx.Client(timeout=30) as client:
            resp = client.post(
                api_url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200,
                    "temperature": 0.7
                }
            )
            if resp.status_code != 200:
                return {"success": False, "msg": f"AI 服务返回错误: HTTP {resp.status_code}"}
            result = resp.json()
            desc = result["choices"][0]["message"]["content"].strip()
            # 去掉可能的引号包裹
            if desc.startswith('"') and desc.endswith('"'):
                desc = desc[1:-1]
            return {"success": True, "description": desc}
    except httpx.TimeoutException:
        return {"success": False, "msg": "AI 服务响应超时，请稍后重试"}
    except Exception as e:
        return {"success": False, "msg": f"AI 调用失败: {str(e)}"}

class AIConfigUpdate(BaseModel):
    ai_api_key: str = ""
    ai_api_url: str = ""
    ai_model: str = ""

@app.get("/api/ai_config")
def get_ai_config():
    """获取 AI 配置（密钥脱敏）"""
    key = get_config("ai_api_key", "")
    masked_key = key[:8] + "****" + key[-4:] if len(key) > 12 else ("****" if key else "")
    return {
        "ai_api_key_masked": masked_key,
        "ai_api_url": get_config("ai_api_url", "https://goldenglow.webn.cc/v1/chat/completions"),
        "ai_model": get_config("ai_model", "gemini-2.5-flash"),
        "configured": bool(key)
    }

@app.post("/api/ai_config")
def update_ai_config(data: AIConfigUpdate):
    """管理员更新 AI 配置"""
    if data.ai_api_key:
        set_config("ai_api_key", data.ai_api_key)
    if data.ai_api_url:
        set_config("ai_api_url", data.ai_api_url)
    if data.ai_model:
        set_config("ai_model", data.ai_model)
    log_audit("AI_CONFIG_UPDATED", "admin", "ai_config", f"模型: {data.ai_model}")
    return {"success": True, "msg": "AI 配置已更新"}

# ======================== 岗位图片管理 ========================

JOB_IMAGE_DIR = os.path.join(UPLOAD_DIR, "job_images")
CERT_DIR = os.path.join(UPLOAD_DIR, "certifications")
os.makedirs(JOB_IMAGE_DIR, exist_ok=True)
os.makedirs(CERT_DIR, exist_ok=True)

@app.post("/api/upload_job_image")
async def upload_job_image(file: UploadFile = File(...)):
    """上传岗位图片"""
    ext = file.filename.split(".")[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "gif", "webp"):
        return {"success": False, "msg": "仅支持 jpg/png/gif/webp 格式"}
    filename = f"job_{int(time.time())}_{os.urandom(4).hex()}.{ext}"
    save_path = os.path.join(JOB_IMAGE_DIR, filename)
    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"success": True, "url": f"http://127.0.0.1:8000/uploads/job_images/{filename}"}

class JobImageSave(BaseModel):
    job_id: int
    image_urls: list

@app.post("/api/save_job_images")
def save_job_images(data: JobImageSave):
    """保存岗位图片记录"""
    with engine.connect() as conn:
        for url in data.image_urls:
            conn.execute(text(
                "INSERT INTO job_images (job_id, image_url) VALUES (:jid, :url)"
            ), {"jid": data.job_id, "url": url})
        conn.commit()
    return {"success": True}

@app.get("/api/job_images/{job_id}")
def get_job_images(job_id: int):
    """获取岗位图片列表"""
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, image_url, created_at FROM job_images WHERE job_id=:jid ORDER BY id"
        ), {"jid": job_id}).fetchall()
        return [{"id": r[0], "url": r[1], "created_at": str(r[2])} for r in rows]

@app.get("/api/job_detail/{job_id}")
def get_job_detail_full(job_id: int):
    """获取岗位完整详情（含图片和教师信息）"""
    with engine.connect() as conn:
        job = conn.execute(text("""
            SELECT j.id, j.chain_job_id, j.title, COALESCE(j.department,''),
                   COALESCE(j.hourly_rate_wei,'0'), j.salary_wei, j.employer_address,
                   j.status, COALESCE(j.description,''), j.created_at
            FROM jobs j WHERE j.id=:jid
        """), {"jid": job_id}).fetchone()
        if not job:
            return {"success": False, "msg": "岗位不存在"}

        # 教师信息
        teacher = conn.execute(text(
            "SELECT nickname, avatar FROM users WHERE wallet_address=:w"
        ), {"w": job[6]}).fetchone()

        # 图片
        imgs = conn.execute(text(
            "SELECT image_url FROM job_images WHERE job_id=:jid ORDER BY id"
        ), {"jid": job_id}).fetchall()

        # 申请人数
        app_count = conn.execute(text(
            "SELECT COUNT(*) FROM employments WHERE job_id=:jid"
        ), {"jid": job_id}).scalar() or 0

        return {
            "success": True,
            "data": {
                "id": job[0], "chain_job_id": job[1], "title": job[2],
                "department": job[3],
                "hourly_rate": str(Web3.from_wei(int(job[4]), 'ether')) if job[4] and job[4] != '0' else '0',
                "salary": str(Web3.from_wei(int(job[5]), 'ether')),
                "employer": job[6], "status": job[7], "description": job[8],
                "created_at": str(job[9]),
                "teacher_nickname": teacher[0] if teacher else "未知",
                "teacher_avatar": teacher[1] if teacher else "",
                "images": [r[0] for r in imgs],
                "applicant_count": app_count
            }
        }

# ======================== 佐证材料上传 ========================

@app.post("/api/upload_certification")
async def upload_certification(file: UploadFile = File(...)):
    """学生上传佐证材料"""
    ext = file.filename.split(".")[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "gif", "webp", "pdf", "doc", "docx"):
        return {"success": False, "msg": "仅支持 jpg/png/pdf/doc 格式"}
    filename = f"cert_{int(time.time())}_{os.urandom(4).hex()}.{ext}"
    save_path = os.path.join(CERT_DIR, filename)
    with open(save_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"success": True, "url": f"/uploads/certifications/{filename}", "filename": file.filename}

@app.get("/api/admin/certifications")
def get_certifications():
    """管理员查看所有佐证材料"""
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT p.id, p.wallet_address, p.certification_hash, p.financial_level,
                   COALESCE(u.nickname,''), p.created_at
            FROM student_profiles p JOIN users u ON p.user_id = u.id
            WHERE p.certification_hash IS NOT NULL AND p.certification_hash != ''
            ORDER BY p.created_at DESC
        """)).fetchall()
        return [{"id": r[0], "wallet": r[1], "cert_url": r[2], "financial_level": r[3],
                 "nickname": r[4], "created_at": str(r[5])} for r in rows]

# ======================== 模拟攻击（仅供演示） ========================

ATTACK_SCENARIOS = [
    {"name": "SQL注入攻击", "target": "/api/login", "payload": "' OR 1=1 --", "severity": "高危"},
    {"name": "XSS跨站脚本", "target": "/api/save_job", "payload": "<script>alert('xss')</script>", "severity": "中危"},
    {"name": "CSRF跨站请求伪造", "target": "/api/approve_application", "payload": "伪造审批请求", "severity": "高危"},
    {"name": "DDoS流量洪泛", "target": "/api/public_jobs", "payload": "10000 req/s", "severity": "严重"},
    {"name": "路径遍历攻击", "target": "/uploads/../../etc/passwd", "payload": "../../../etc/passwd", "severity": "高危"},
    {"name": "合约重入攻击", "target": "settleSalary()", "payload": "fallback() loop", "severity": "严重"},
    {"name": "私钥窃取尝试", "target": "MetaMask", "payload": "eth_requestAccounts flood", "severity": "严重"},
    {"name": "未授权访问", "target": "/api/admin/stats", "payload": "无Token直接访问", "severity": "中危"},
    {"name": "SSRF服务端请求伪造", "target": "/api/ai_generate_desc", "payload": "http://169.254.169.254", "severity": "高危"},
    {"name": "文件上传漏洞", "target": "/api/upload_avatar", "payload": "shell.php.jpg", "severity": "高危"},
]

@app.post("/api/simulate_attack")
def simulate_attack():
    """模拟安全攻击（纯演示，不执行任何真实攻击）"""
    import random
    scenario = random.choice(ATTACK_SCENARIOS)
    blocked = random.choice([True, True, True, False])  # 75% blocked
    return {
        "success": True,
        "attack_name": scenario["name"],
        "target": scenario["target"],
        "payload": scenario["payload"],
        "severity": scenario["severity"],
        "blocked": blocked,
        "message": f"{'已拦截' if blocked else '检测到'} {scenario['name']} → {scenario['target']}"
    }

# ======================== 静态文件挂载 (必须放最后) ========================

app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
