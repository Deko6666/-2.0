# 高校勤工助学薪酬自动管理系统

基于区块链的高校勤工俭学管理系统：岗位发布、学生申请、教师审批、工时打卡、薪酬自动结算全流程上链，后端负责业务编排与数据持久化，前端提供多角色操作界面。

- **区块链**：Geth Dev Chain（Chain ID 自动适配节点实际值（geth --dev 为 `1337` / `0x539`），RPC `http://127.0.0.1:8545`）
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
├── png/                     # 校徽/展开图素材（/png 静态挂载）
├── frontend/images/         # 前端图片（校徽副本、主页配图）
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
| Geth | 1.10+ | 本地开发链，Chain ID 自动适配（--dev 为 1337） |
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
- Chain ID：自动适配节点实际值（geth --dev 为 `1337`）
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

脚本依次执行：清理残留后端进程 → **自动清空演示业务数据（无需确认）** → 部署合约并重新播种演示岗位/历史数据 → 启动后端 → **等接口就绪后**自动打开浏览器。

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
| `STUDENT` | 浏览/申请多个岗位、打卡上下班、迟到/早退卡、积分兑换助学金、缴纳罚款/申诉、查看薪酬记录、辞职 |
| `TEACHER` | 发布岗位（部门下拉选择）、审批申请、确认工时、发放薪酬、颁发勋章、查看我发布的岗位、接收离职通知 |
| `ADMIN` | 用户/岗位管理、系统统计、奖惩管理（一键扣钱）、封禁与解封、异常打卡与超时提醒、结算配置、审计日志 |

教师身份在链上通过 `registerAsTeacher()` 白名单登记；后端角色存 MySQL `users.role`。

---

## 智能合约核心流程

```
教师注册 ─▶ createJob (锁仓预算)
              │
学生申请 ─▶ applyForJob ─▶ [PENDING] ─▶ 算法匹配度评分(TOPSIS)
              │
教师审批 ─▶ approveApplication ─▶ [APPROVED] ─▶ 打卡 clockIn / clockOut
              │                                        │
              │                         （工时 = block.timestamp 自动计算，学生无法填时长）
              │                         （IP记录 + 风险检测 + 工时红线拦截）
              │                                        │
              │                              confirmHours (确认工时)
              │                                        │
              └── rejectApplication ─▶ [REJECTED]   settleSalary (发放薪酬)
                                                   │
                                   closeJob / 颁发SBT勋章 (ERC-5192)
                                                   │
                                   学生可对驳回发起争议申诉 → 管理员仲裁
```

**防舞弊计时机制**：
- `clockOut` 不再接受手动传入的工时，由合约按 `block.timestamp - clockInTime` 自动计算（向下取整到分钟），前端仅展示实时计时器；演示模式下按 **现实 1 秒 = 系统 15 分钟** 倍速计时（单次上限 8 小时约 32 秒走完）
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
| **精准资助** | `POST /api/student_profile` `GET /api/student_profile/{wallet}` `GET /api/match_score/{eid}` `GET /api/admin/verify_profiles` `POST /api/admin/verify_profile` |
| **规范风控** | `GET /api/student_hour_status/{wallet}` `GET /api/risk_alerts` |
| **SBT勋章** | `GET /api/badges/{wallet}` `GET /api/verify_badge/{token_id}` |
| **争议仲裁** | `POST /api/disputes` `GET /api/disputes/{wallet}` `GET /api/admin/disputes` `POST /api/admin/resolve_dispute` |
| **积分/奖惩** | `GET /api/points/{wallet}` `POST /api/exchange_points` `POST /api/penalty_event` `GET /api/rewards_penalties` `POST /api/reward_penalty` `POST /api/rewards_penalties/seen` `POST /api/admin_deduct` |
| **罚款/封禁** | `GET /api/student_penalties/{wallet}` `POST /api/pay_fine` `POST /api/appeal` `GET /api/bans` `POST /api/unban` |
| **通知/演示** | `GET /api/notifications/{role}` `POST /api/notifications/seen` `GET /api/employer_jobs/{address}` `GET /api/abnormal_clock_ins` `GET /api/public_jobs` |

---

## 进阶功能

### 一、精准资助：人岗智能撮合算法（TOPSIS）

教育部要求勤工助学岗位优先保障家庭经济困难学生。系统实现了多属性加权TOPSIS算法进行人岗匹配：

**Score = w₁×困难等级 + w₂×课余时间匹配度 + w₃×历史信誉分 − w₄×本月已获助学金**

| 指标 | 权重 | 说明 |
|---|---|---|
| 困难等级 | 0.35 | A级=1.0, B级=0.7, C级=0.4, 普通=0.1 |
| 课余时间匹配度 | 0.25 | min(可用工时/岗位需求工时, 1.0) |
| 历史履约信誉分 | 0.25 | 0-100归一化 |
| 本月已获助学金 | 0.15 | 负向指标，越低越好 |

教师审批时展示算法匹配度百分比，困难生自动置顶。

### 二、SBT履约存证（ERC-5192灵魂绑定凭证）

基于ERC-5192规范（Locked Soulbound Token）实现不可转让的链上履约勋章：

- `contracts/WorkStudySBT.sol`：精简ERC-721实现 + soulbound锁定
- 岗位结项时教师颁发勋章，记录岗位名称、部门、累计工时、评语、星级
- 公开验真接口，任何人可查询勋章真伪
- 代币绑定学生钱包，禁止 transfer/交易

### 三、规范风控：工时红线与防代刷

教育部政策合规红线（演示版）："每日不超过 8 小时，每月不超过 240 小时（8h×30 天）"

- **工时红线**：打卡时检查日/月配额（按打卡记录实时统计），超限自动拦截；工作超时会在管理员页弹出"工作时间过长影响学业"提醒
- **IP异常检测**：打卡记录客户端IP，上下班IP跨网段时标记 `IP_MISMATCH`
- **进度条可视化**：学生端展示今日/本月工时配额使用情况

### 四、权益闭环：争议申诉与仲裁

教师驳回打卡后，学生可在72小时内发起申诉：

1. 学生填写申诉理由，提交佐证材料哈希
2. 状态变为 `IN_DISPUTE`
3. 管理员在仲裁面板调阅双方流水，一键裁决：
   - **维持驳回**：记录保持墓碑状态
   - **强制通过**：恢复工时并累加，同步到薪酬

---

## pro-1.0 演示版新特性

### 启动与演示数据（重启即恢复初始状态）
- `start.bat` 免确认自动清库，并按**进程树**清理残留后端，接口就绪后才开浏览器
- 自动播种 **10 个演示岗位**（挂在演示教师账户下，链上同步创建）+ 演示学生近 14 天打卡、近 6 个月结算、积分与奖惩示例
- **三个默认账户**：管理员 `0xf39F…2266`、教师 `0x7099…79C8`、学生 `0x3C44…93BC`（钱包直接登录）
- 学生可**同时任职多个岗位**（已放开单岗位限制）

### 打卡体验
- 计时演示模式：**现实 1 秒 = 系统 15 分钟**，单次上限 8 小时约 32 秒走完
- **打卡悬浮坞**：上班后常驻“下班打卡”入口，跨页面/刷新不丢
- **迟到卡 / 早退卡**：打卡页一键记录（进奖惩页），打完**立即收工**并停止计时
- 链上交易 nonce 冲突**自动重试**，打卡/下班/发布不再需要点第二次

### 红线与异常提醒
- 日 8 小时 / 月 240 小时红线，实时拦截
- **忘记打下班卡**（超上限半小时）与**工作时间过长影响学业** → 管理员页自动弹窗
- 学生辞职 → 管理员/教师**登录弹窗通知**（阅后即消）

### 积分 · 奖惩 · 封禁
- 加班积分：打卡超 4 小时的部分每满 1 小时 +1 分
- 积分兑换助学金：10 分=¥100 / 50 分=¥500 / 100 分=¥1000（1 分=¥10）
- 奖惩管理（管理员）：迟到/早退事件自动进流水，**一键扣钱**；余额不足**自动封禁**
- 学生端“我的奖惩”：缴纳罚款 / 提交申诉；被封号无法登录（提示联系指导老师），**解封卡**一键解除；封禁/罚款/申诉记录**重启即清**

### UI / UX 重制（暗金主题）
- 统一暗金弹窗（确认/输入/五角星评分，星星带弹跳+震动+粒子散落特效）替代所有原生弹窗
- 校徽圆形金环展示；头像暗金渐变底；全部数值统一 2 位小数
- 用人部门**可下拉可输入**（图书部/宿舍部/食堂部/门岗部/清洁部…）
- 顶部校训「勤思不忘体力行，他助仍需己坚奋」与侧边栏「砥砺青春，奋斗前行，以勤为伴，与爱同行」，点击触发“劳动最光荣”飘洒特效

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
会，默认**自动清空全部演示业务数据**（jobs / employments / clock_ins / settlements / hour_limits / 奖惩 / 罚款申诉 / 封禁 / 账户余额）且无需确认，每次启动恢复到初始演示状态；用户表始终保留。
