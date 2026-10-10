# 高校勤工助学薪酬自动管理系统

基于区块链的高校勤工俭学管理系统：岗位发布、学生申请、教师审批、工时打卡、薪酬自动结算全流程上链，后端负责业务编排与数据持久化，前端提供多角色操作界面。

- **区块链**：Geth Dev Chain（Chain ID `887766` / `0xD8BD6`，RPC `http://127.0.0.1:8545`）
- **智能合约**：`contracts/WorkStudy.sol`（Solidity 0.8.20）
- **后端**：FastAPI + web3.py + MySQL（SQLAlchemy），端口 `8000`
- **前端**：单文件 `frontend/index.html`（ethers.js 6.7 + 原生 JS），由 FastAPI 直接托管

---

## 系统架构

```
┌─────────────────┐     HTTP /api      ┌──────────────────┐     JSON-RPC     ┌──────────────┐
│   浏览器前端     │ ─────────────────▶ │   FastAPI 后端    │ ───────────────▶ │  Geth 链节点  │
│  index.html     │ ◀───────────────── │   main.py        │ ◀─────────────── │  WorkStudy   │
│  (ethers.js)    │    MetaMask 交易    │   + MySQL        │   读链/自动结算   │   合约       │
└─────────────────┘                    └──────────────────┘                  └──────────────┘
        │                                      │
        └────────── MetaMask 直连签名 ──────────┘
```

- **写操作**（发布岗位、申请、审批、打卡、结算）：前端经 MetaMask 签名后发交易上链，成功后回调后端写入 MySQL
- **读操作**（岗位列表、看板、审计日志）：后端读 MySQL，保证展示速度
- **自动结算**：后端线程按周期调用 `executeAutoSettlement()` / `autoConfirmStaleClockIns()`

---

## 目录结构

```
Campus/
├── contracts/
│   └── WorkStudy.sol        # 核心智能合约
├── frontend/
│   └── index.html           # 前端单页应用
├── scripts/
│   └── deploy.js            # 合约部署脚本（动态 gas + 部署校验）
├── main.py                  # FastAPI 后端（含数据库初始化、全部 API）
├── start.bat                # Windows 一键启动
├── hardhat.config.js        # Hardhat 配置（连 Geth localhost）
├── uploads/                 # 用户上传的头像（运行时生成）
├── contract_address.txt     # 当前部署的合约地址（deploy.js 生成）
└── venv/                    # Python 虚拟环境
```

---

## 环境依赖

| 依赖 | 版本要求 | 说明 |
|---|---|---|
| Geth | 1.10+ | 本地开发链，Chain ID 887766 |
| Node.js | 18+ | 运行 Hardhat / deploy.js |
| Python | 3.10+ | 后端 |
| MySQL | 5.7+ / 8.0 | 库名 `campus_db`，root 密码 `123456` |
| MetaMask | 浏览器扩展 | 前端签名交易 |

Python 依赖（`venv/` 中已装）：`fastapi` `uvicorn` `web3` `sqlalchemy` `pymysql`

---

## 快速开始

### 1. 启动 Geth 开发链

```bash
geth --dev --http --http.addr 0.0.0.0 --http.port 8545 \
     --http.api eth,net,web3,personal,miner \
     --http.corsdomain "*" --datadir geth_data
```

启动后 MetaMask 添加网络：
- RPC URL：`http://127.0.0.1:8545`
- Chain ID：`887766`
- 货币符号：`ETH`

### 2. 初始化数据库

MySQL 中创建 `campus_db` 库。首次启动后端时会自动建表：

```sql
CREATE DATABASE campus_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

### 3. 一键启动

```bat
start.bat
```

脚本依次执行：清空业务表（可选）→ 部署合约 → 启动后端 → 打开浏览器。

### 4. 手动启动（可选）

```bash
# 部署合约
npx hardhat run scripts/deploy.js --network localhost

# 启动后端
venv\Scripts\activate
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

启动后访问：
- 前端：http://127.0.0.1:8000
- API 文档：http://127.0.0.1:8000/docs

---

## 用户角色

| 角色 | 能力 |
|---|---|
| `STUDENT` | 浏览岗位、申请、打卡/打卡下班、查看工时与结算、辞职 |
| `TEACHER` | 发布岗位（需锁定预算）、审批申请、确认工时、发放薪酬、关闭岗位 |
| `ADMIN` | 用户管理、岗位管理、系统统计、结算配置、审计日志、链上 ID 修复 |

教师身份在链上通过 `registerAsTeacher()` 白名单登记；后端角色存 MySQL `users.role`。

---

## 智能合约核心流程

```
教师注册 ─▶ createJob (锁仓预算)
              │
学生申请 ─▶ applyForJob ─▶ [PENDING]
              │
教师审批 ─▶ approveApplication ─▶ [APPROVED] ─▶ 打卡 clockIn / clockOut
              │                                        │
              │                         （工时 = block.timestamp 自动计算，学生无法填时长）
              │                                        │
              │                              confirmHours (确认工时)
              │                                        │
              └── rejectApplication ─▶ [REJECTED]   settleSalary (发放薪酬)
                                                   │
                                   closeJob (退还剩余预算) / resignFromJob
```

**防舞弊计时机制**：
- `clockOut` 不再接受手动传入的工时，由合约按 `block.timestamp - clockInTime` 自动计算（向下取整到分钟），前端仅展示实时计时器
- 单次打卡上限 8 小时（480 分钟）：超出部分截断而非回滚，忘记下班不会卡死学生
- `rejectClockIn` 链上墓碑（`workMinutes=0, confirmed=true`）：教师驳回的记录不会被宽限期自动确认、不会计薪

**自动结算机制**：
- `settlementConfig.cycleDays`：结算周期（天）
- `gracePeriodHours`：工时确认宽限期，超时由 `autoConfirmStaleClockIns()` 自动确认（被 `rejectClockIn` 驳回的记录因墓碑标记不会命中）
- `isSettlementDue()` 到期后由 `autoSettler` 调用 `executeAutoSettlement()` 批量结算

主要事件：`JobCreated` / `ApplicationSubmitted` / `ApplicationApproved` / `ClockedIn` / `HoursConfirmed` / `SalarySettled` / `AutoSettlementExecuted`

---

## 主要 API

完整文档见 http://127.0.0.1:8000/docs

| 分类 | 端点 |
|---|---|
| 认证 | `POST /api/login` `POST /api/register` `POST /api/login_by_wallet` `POST /api/bind_wallet` |
| 岗位 | `POST /api/save_job` `GET /api/public_jobs` `GET /api/job/{id}` `POST /api/delete_job` |
| 申请 | `POST /api/apply_job` `POST /api/approve_application` `POST /api/reject_application` `POST /api/resign` |
| 打卡 | `POST /api/clock_in` `POST /api/clock_out` `POST /api/confirm_hours` `POST /api/reject_clock_in` |

> **打卡工时说明**：`clock_in` 响应含 `clock_in_time`（前端计时器起点）；`clock_out` 请求只需 `clock_in_id`，工时由链上/服务端按时间戳自动计算，响应返回 `work_minutes`。
| 结算 | `POST /api/settle_salary` `GET /api/settlements/{wallet}` `GET /api/pending_settlements/{employer}` |
| 看板 | `GET /api/my_dashboard/{wallet}` `GET /api/teacher_dashboard/{wallet}` |
| 管理 | `GET /api/admin/stats` `GET /api/admin/users` `GET /api/admin/jobs` `POST /api/admin/delete_all_jobs` |
| 自动结算 | `GET /api/settlement_config` `POST /api/settlement_config` `POST /api/trigger_auto_settlement` |
| 其他 | `GET /api/audit_logs` `POST /api/repair_chain_ids` `POST /api/upload_avatar` |

---

## 部署脚本说明

`scripts/deploy.js` 通过硬编码 JSON-RPC 与 Geth 通信（非 Hardhat 内置网络），做了三重校验防止部署失败却写入死地址：

1. `eth_estimateGas` × 1.3 动态计算 gas（WorkStudy 部署约需 525 万）
2. 校验 `receipt.status === 0x1`
3. `eth_getCode` 确认地址上确实存在代码

部署成功后自动更新 `frontend/index.html` 的 `CONTRACT_ADDR` 和根目录 `contract_address.txt`。

> **教训**：早期版本固定 `gas: 400万` 导致部署 out-of-gas，但 Geth 对失败的部署也返回 `contractAddress`，脚本未检查 status 就把死地址写进前端，后续所有"合约调用"实为向空地址转账，ETH 直接丢失。**新部署脚本必须校验 status + getCode。**

---

## 常见问题

**合约调用"成功"但没有日志、view 返回 `0x`？**
先执行 `eth_getCode` 确认合约地址上有代码，大概率部署失败写入了死地址。

**MetaMask 交易卡住/失败？**
Geth dev 模式下账户需解锁。部署账户是 dev 预解锁账户；MetaMask 账户发交易需用 `personal.unlockAccount` 或改用签名方式。

**浏览器控制台报 `content_main.js toLowerCase`？**
Edge 扩展导致，与本项目无关。

**学生能虚报打卡时长吗？**
不能。工时由合约按 `block.timestamp` 自动计算，前端只显示实时计时器，没有填写入口。打卡后 60 秒内下班记 0 分钟；忘记下班超 8 小时的按 480 分钟截断（教师可在待确认列表驳回异常记录，驳回后链上墓碑保证不会被自动结算计薪）。

**`start.bat` 会清空数据吗？**
不会默认清空。脚本启动时会询问是否清空业务表（jobs / employments / clock_ins / settlements），**默认保留数据**（直接回车）；输入 `Y` 才清空。用户表始终保留。
