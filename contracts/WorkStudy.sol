// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @title WorkStudy - 高校勤工助学薪酬自动管理合约
 * @notice 实现岗位发布、学生申请、教师审批、工时打卡、薪酬自动结算的完整流程
 */
contract WorkStudy {

    // ======================== 数据结构 ========================

    struct Job {
        uint256 id;
        string title;
        string department;      // 用人部门
        uint256 hourlyRate;     // 时薪 (Wei)
        address employer;       // 教师地址
        uint256 totalBudget;    // 总预算 (Wei)
        uint256 remainingBudget;// 剩余预算
        JobStatus status;
        uint256 createdAt;
    }

    struct Employment {
        uint256 id;
        uint256 jobId;
        address student;
        EmploymentStatus status;
        uint256 totalHours;     // 累计工时 (分钟)
        uint256 appliedAt;
        uint256 approvedAt;
    }

    struct ClockInRecord {
        uint256 id;
        uint256 employmentId;
        address student;
        uint256 clockInTime;
        uint256 clockOutTime;
        uint256 workMinutes;
        bool confirmed;         // 教师是否确认
    }

    struct Settlement {
        uint256 id;
        uint256 employmentId;
        address student;
        uint256 amount;         // 结算金额 (Wei)
        uint256 hoursSettled;   // 结算工时 (分钟)
        uint256 settledAt;
    }

    enum JobStatus { OPEN, FILLED, CLOSED }
    enum EmploymentStatus { PENDING, APPROVED, REJECTED, COMPLETED }

    // ======================== 状态变量 ========================

    mapping(uint256 => Job) public jobs;
    mapping(uint256 => Employment) public employments;
    mapping(uint256 => ClockInRecord) public clockIns;
    mapping(uint256 => Settlement) public settlements;

    uint256 public jobCount;
    uint256 public employmentCount;
    uint256 public clockInCount;
    uint256 public settlementCount;

    // 教师地址白名单 (简化版，实际可用AccessControl)
    mapping(address => bool) public isTeacher;

    // ======================== 自动结算配置 ========================

    struct SettlementConfig {
        uint256 cycleDays;          // 结算周期（天），如30天
        uint256 lastSettlementTime; // 上次自动结算时间
        bool autoEnabled;           // 是否开启自动结算
        uint256 gracePeriodHours;   // 工时确认宽限期（小时），超时自动确认
    }

    SettlementConfig public settlementConfig;

    // 自动结算执行者地址（后端服务）
    address public autoSettler;

    // 管理员
    address public admin;

    // ======================== 事件 ========================

    event JobCreated(uint256 indexed jobId, string title, uint256 hourlyRate, address employer);
    event JobClosed(uint256 indexed jobId, address employer, uint256 refundAmount);
    event ApplicationSubmitted(uint256 indexed employmentId, uint256 indexed jobId, address student);
    event ApplicationApproved(uint256 indexed employmentId, address student);
    event ApplicationRejected(uint256 indexed employmentId, address student);
    event StudentResigned(uint256 indexed employmentId, address student);
    event ClockedIn(uint256 indexed clockInId, uint256 indexed employmentId, address student, uint256 workMinutes);
    event HoursConfirmed(uint256 indexed clockInId, uint256 indexed employmentId, uint256 workMinutes);
    event ClockInRejected(uint256 indexed clockInId, uint256 indexed employmentId, address student);
    event SalarySettled(uint256 indexed settlementId, uint256 indexed employmentId, address student, uint256 amount);
    event AutoSettlementExecuted(uint256 timestamp, uint256 totalSettled, uint256 totalAmount);
    event HoursAutoConfirmed(uint256 indexed clockInId, uint256 indexed employmentId, uint256 workMinutes);
    event SettlementConfigUpdated(uint256 cycleDays, bool autoEnabled, uint256 gracePeriodHours);
    event AutoSettlerUpdated(address indexed oldSettler, address indexed newSettler);

    // ======================== 修饰符 ========================

    modifier onlyEmployer(uint256 _jobId) {
        require(msg.sender == jobs[_jobId].employer, "Only the employer can perform this action");
        _;
    }

    modifier onlyStudent(uint256 _employmentId) {
        require(msg.sender == employments[_employmentId].student, "Only the student can perform this action");
        _;
    }

    modifier onlyAdmin() {
        require(msg.sender == admin, "Only admin can perform this action");
        _;
    }

    modifier onlyAutoSettler() {
        require(msg.sender == autoSettler || msg.sender == admin, "Only auto-settler or admin");
        _;
    }

    // ======================== 初始化 ========================

    constructor() {
        admin = msg.sender;
        autoSettler = msg.sender;
        settlementConfig = SettlementConfig({
            cycleDays: 30,
            lastSettlementTime: block.timestamp,
            autoEnabled: true,
            gracePeriodHours: 48
        });
    }

    // ======================== 自动结算管理 ========================

    /**
     * @notice 管理员更新结算配置
     * @param _cycleDays 结算周期（天）
     * @param _autoEnabled 是否开启自动结算
     * @param _gracePeriodHours 工时确认宽限期（小时）
     */
    function updateSettlementConfig(
        uint256 _cycleDays,
        bool _autoEnabled,
        uint256 _gracePeriodHours
    ) external onlyAdmin {
        require(_cycleDays >= 1 && _cycleDays <= 365, "Invalid cycle days");
        settlementConfig.cycleDays = _cycleDays;
        settlementConfig.autoEnabled = _autoEnabled;
        settlementConfig.gracePeriodHours = _gracePeriodHours;
        emit SettlementConfigUpdated(_cycleDays, _autoEnabled, _gracePeriodHours);
    }

    /**
     * @notice 更新自动结算执行者地址
     */
    function setAutoSettler(address _settler) external onlyAdmin {
        emit AutoSettlerUpdated(autoSettler, _settler);
        autoSettler = _settler;
    }

    /**
     * @notice 检查是否到达自动结算时间
     */
    function isSettlementDue() public view returns (bool) {
        return block.timestamp >= settlementConfig.lastSettlementTime + settlementConfig.cycleDays * 1 days;
    }

    /**
     * @notice 获取待结算的就业合约ID列表（有已确认工时的）
     */
    function getPendingSettlementIds() external view returns (uint256[] memory) {
        uint256 count = 0;
        for (uint256 i = 1; i <= employmentCount; i++) {
            if (employments[i].status == EmploymentStatus.APPROVED && employments[i].totalHours > 0) {
                count++;
            }
        }
        uint256[] memory ids = new uint256[](count);
        uint256 idx = 0;
        for (uint256 i = 1; i <= employmentCount; i++) {
            if (employments[i].status == EmploymentStatus.APPROVED && employments[i].totalHours > 0) {
                ids[idx++] = i;
            }
        }
        return ids;
    }

    /**
     * @notice 自动确认超时的工时打卡（超过宽限期未确认的自动确认）
     */
    function autoConfirmStaleClockIns() external onlyAutoSettler {
        uint256 graceSeconds = settlementConfig.gracePeriodHours * 1 hours;
        uint256 confirmedCount = 0;
        for (uint256 i = 1; i <= clockInCount; i++) {
            ClockInRecord storage record = clockIns[i];
            if (!record.confirmed && record.clockOutTime > 0 && record.workMinutes > 0) {
                if (block.timestamp >= record.clockOutTime + graceSeconds) {
                    record.confirmed = true;
                    employments[record.employmentId].totalHours += record.workMinutes;
                    emit HoursAutoConfirmed(i, record.employmentId, record.workMinutes);
                    confirmedCount++;
                }
            }
        }
    }

    /**
     * @notice 批量自动结算薪酬 - 到期自动触发
     * @dev 由autoSettler或管理员调用，自动核算并发放所有待结算薪酬
     */
    function _executeAutoSettlement() internal {
        require(settlementConfig.autoEnabled, "Auto settlement is disabled");

        // 先自动确认超时工时
        uint256 graceSeconds = settlementConfig.gracePeriodHours * 1 hours;
        for (uint256 i = 1; i <= clockInCount; i++) {
            ClockInRecord storage record = clockIns[i];
            if (!record.confirmed && record.clockOutTime > 0 && record.workMinutes > 0) {
                if (block.timestamp >= record.clockOutTime + graceSeconds) {
                    record.confirmed = true;
                    employments[record.employmentId].totalHours += record.workMinutes;
                    emit HoursAutoConfirmed(i, record.employmentId, record.workMinutes);
                }
            }
        }

        // 自动结算所有有工时的就业合约
        uint256 totalSettled = 0;
        uint256 totalAmount = 0;

        for (uint256 e = 1; e <= employmentCount; e++) {
            Employment storage emp = employments[e];
            if (emp.status != EmploymentStatus.APPROVED || emp.totalHours == 0) continue;

            Job storage job = jobs[emp.jobId];
            uint256 salary = (emp.totalHours * job.hourlyRate) / 60;
            if (salary == 0 || job.remainingBudget < salary) continue;

            job.remainingBudget -= salary;
            settlementCount++;
            settlements[settlementCount] = Settlement({
                id: settlementCount,
                employmentId: e,
                student: emp.student,
                amount: salary,
                hoursSettled: emp.totalHours,
                settledAt: block.timestamp
            });

            emp.totalHours = 0;
            totalSettled++;
            totalAmount += salary;

            (bool success, ) = payable(emp.student).call{value: salary}("");
            require(success, "Payment transfer failed");

            emit SalarySettled(settlementCount, e, emp.student, salary);
        }

        settlementConfig.lastSettlementTime = block.timestamp;
        emit AutoSettlementExecuted(block.timestamp, totalSettled, totalAmount);
    }

    /**
     * @notice 外部触发自动结算（autoSettler/管理员）
     */
    function executeAutoSettlement() external onlyAutoSettler {
        _executeAutoSettlement();
    }

    /**
     * @notice 手动触发立即结算（管理员/教师）
     */
    function triggerImmediateSettlement() external {
        require(msg.sender == admin || isTeacher[msg.sender], "Only admin or teacher");
        _executeAutoSettlement();
    }

    // ======================== 教师功能 ========================

    /**
     * @notice 教师设置自己为教师身份
     */
    function registerAsTeacher() external {
        isTeacher[msg.sender] = true;
    }

    /**
     * @notice 教师发布岗位，同时锁仓薪酬预算
     * @param _title 岗位标题
     * @param _department 用人部门
     * @param _hourlyRate 时薪 (Wei)
     */
    function createJob(
        string memory _title,
        string memory _department,
        uint256 _hourlyRate
    ) external payable {
        require(msg.value > 0, "Must deposit budget");
        require(_hourlyRate > 0, "Hourly rate must be greater than 0");
        require(isTeacher[msg.sender], "Only teachers can create jobs");

        jobCount++;
        jobs[jobCount] = Job({
            id: jobCount,
            title: _title,
            department: _department,
            hourlyRate: _hourlyRate,
            employer: msg.sender,
            totalBudget: msg.value,
            remainingBudget: msg.value,
            status: JobStatus.OPEN,
            createdAt: block.timestamp
        });

        emit JobCreated(jobCount, _title, _hourlyRate, msg.sender);
    }

    /**
     * @notice 教师审批学生的岗位申请
     * @param _employmentId 任职合约ID
     */
    function approveApplication(uint256 _employmentId) external {
        Employment storage emp = employments[_employmentId];
        require(emp.id != 0, "Employment does not exist");
        require(emp.status == EmploymentStatus.PENDING, "Application not pending");
        require(msg.sender == jobs[emp.jobId].employer, "Only employer can approve");

        emp.status = EmploymentStatus.APPROVED;
        emp.approvedAt = block.timestamp;

        // 标记岗位为已填充
        jobs[emp.jobId].status = JobStatus.FILLED;

        emit ApplicationApproved(_employmentId, emp.student);
    }

    /**
     * @notice 教师拒绝学生的岗位申请
     */
    function rejectApplication(uint256 _employmentId) external {
        Employment storage emp = employments[_employmentId];
        require(emp.id != 0, "Employment does not exist");
        require(emp.status == EmploymentStatus.PENDING, "Application not pending");
        require(msg.sender == jobs[emp.jobId].employer, "Only employer can reject");

        emp.status = EmploymentStatus.REJECTED;
        emit ApplicationRejected(_employmentId, emp.student);
    }

    /**
     * @notice 教师确认学生的工时打卡记录
     * @param _clockInId 打卡记录ID
     */
    function confirmHours(uint256 _clockInId) external {
        ClockInRecord storage record = clockIns[_clockInId];
        require(record.id != 0, "Clock-in record does not exist");
        require(!record.confirmed, "Already confirmed");
        require(
            msg.sender == jobs[employments[record.employmentId].jobId].employer,
            "Only employer can confirm"
        );

        record.confirmed = true;

        // 累加工时到任职合约
        employments[record.employmentId].totalHours += record.workMinutes;

        emit HoursConfirmed(_clockInId, record.employmentId, record.workMinutes);
    }

    /**
     * @notice 教师驳回学生的工时打卡记录（链上墓碑，防止被自动结算误发放）
     * @param _clockInId 打卡记录ID
     */
    function rejectClockIn(uint256 _clockInId) external {
        ClockInRecord storage record = clockIns[_clockInId];
        require(record.id != 0, "Clock-in record does not exist");
        require(!record.confirmed, "Already processed");
        require(
            msg.sender == jobs[employments[record.employmentId].jobId].employer,
            "Only employer can reject"
        );

        // 墓碑：workMinutes=0 且 confirmed=true，
        // 自动确认循环 (!confirmed && workMinutes>0) 不再命中，confirmHours 也会被 !confirmed 挡住
        record.workMinutes = 0;
        record.confirmed = true;

        emit ClockInRejected(_clockInId, record.employmentId, record.student);
    }

    /**
     * @notice 管理员裁决"强制通过"：恢复被驳回的打卡记录，重新计入工时
     * @dev 解决链上 rejectClockIn 不可逆的问题，仅管理员可调用
     * @param _clockInId 打卡记录ID
     * @param _workMinutes 恢复的工时（取自数据库 original_work_minutes）
     */
    function adminConfirmClockIn(uint256 _clockInId, uint256 _workMinutes) external onlyAdmin {
        ClockInRecord storage record = clockIns[_clockInId];
        require(record.id != 0, "Clock-in record does not exist");
        require(record.workMinutes == 0, "Record not rejected");

        record.workMinutes = _workMinutes;
        record.confirmed = true;
        employments[record.employmentId].totalHours += _workMinutes;

        emit HoursConfirmed(_clockInId, record.employmentId, _workMinutes);
    }

    // ======================== 学生功能 ========================

    /**
     * @notice 学生申请岗位
     * @param _jobId 岗位ID
     */
    function applyForJob(uint256 _jobId) external {
        Job storage job = jobs[_jobId];
        require(job.id != 0, "Job does not exist");
        require(job.status == JobStatus.OPEN, "Job is not open");

        employmentCount++;
        employments[employmentCount] = Employment({
            id: employmentCount,
            jobId: _jobId,
            student: msg.sender,
            status: EmploymentStatus.PENDING,
            totalHours: 0,
            appliedAt: block.timestamp,
            approvedAt: 0
        });

        emit ApplicationSubmitted(employmentCount, _jobId, msg.sender);
    }

    /**
     * @notice 学生打卡上班
     * @param _employmentId 任职合约ID
     */
    function clockIn(uint256 _employmentId) external onlyStudent(_employmentId) {
        Employment storage emp = employments[_employmentId];
        require(emp.status == EmploymentStatus.APPROVED, "Employment not approved");

        clockInCount++;
        clockIns[clockInCount] = ClockInRecord({
            id: clockInCount,
            employmentId: _employmentId,
            student: msg.sender,
            clockInTime: block.timestamp,
            clockOutTime: 0,
            workMinutes: 0,
            confirmed: false
        });

        emit ClockedIn(clockInCount, _employmentId, msg.sender, 0);
    }

    /**
     * @notice 学生打卡下班，工时由链上时间戳自动计算（防虚报）
     * @param _clockInId 打卡记录ID
     */
    function clockOut(uint256 _clockInId) external {
        ClockInRecord storage record = clockIns[_clockInId];
        require(record.id != 0, "Clock-in record does not exist");
        require(record.clockOutTime == 0, "Already clocked out");
        require(!record.confirmed, "Already processed");
        require(msg.sender == record.student, "Only the student can clock out");

        // 自动计时：工时 = 当前区块时间 - 上班时间（向下取整，杜绝虚报）
        uint256 workMinutes = (block.timestamp - record.clockInTime) / 60;
        // 单次上限 8 小时：截断而非回滚，防止忘记下班卡死学生
        if (workMinutes > 480) {
            workMinutes = 480;
        }

        record.clockOutTime = block.timestamp;
        record.workMinutes = workMinutes;

        emit ClockedIn(_clockInId, record.employmentId, record.student, workMinutes);
    }

    // ======================== 薪酬结算 ========================

    /**
     * @notice 结算薪酬，自动转账给学生
     * @dev 基于已确认的工时自动计算金额
     * @param _employmentId 任职合约ID
     */
    function settleSalary(uint256 _employmentId) external {
        Employment storage emp = employments[_employmentId];
        require(emp.id != 0, "Employment does not exist");
        require(emp.status == EmploymentStatus.APPROVED, "Employment not approved");
        require(emp.totalHours > 0, "No confirmed hours to settle");
        require(
            msg.sender == jobs[emp.jobId].employer,
            "Only employer can trigger settlement"
        );

        Job storage job = jobs[emp.jobId];
        // 计算薪酬：工时(分钟) × 时薪 / 60
        uint256 salary = (emp.totalHours * job.hourlyRate) / 60;
        require(job.remainingBudget >= salary, "Insufficient budget in contract");

        // 更新预算
        job.remainingBudget -= salary;

        // 创建结算记录
        settlementCount++;
        settlements[settlementCount] = Settlement({
            id: settlementCount,
            employmentId: _employmentId,
            student: emp.student,
            amount: salary,
            hoursSettled: emp.totalHours,
            settledAt: block.timestamp
        });

        // 重置工时
        emp.totalHours = 0;

        // 自动转账给学生
        (bool success, ) = payable(emp.student).call{value: salary}("");
        require(success, "Payment transfer failed");

        emit SalarySettled(settlementCount, _employmentId, emp.student, salary);
    }

    // ======================== 岗位管理 ========================

    /**
     * @notice 教师/管理员关闭岗位，退还剩余预算
     * @param _jobId 岗位ID
     */
    function closeJob(uint256 _jobId) external {
        Job storage job = jobs[_jobId];
        require(job.id != 0, "Job does not exist");
        require(
            msg.sender == job.employer || isTeacher[msg.sender],
            "Only employer or teacher can close"
        );
        require(job.status != JobStatus.CLOSED, "Job already closed");

        uint256 refund = job.remainingBudget;
        job.remainingBudget = 0;
        job.status = JobStatus.CLOSED;

        // 退还剩余预算给教师
        if (refund > 0) {
            (bool success, ) = payable(job.employer).call{value: refund}("");
            require(success, "Refund failed");
        }

        emit JobClosed(_jobId, msg.sender, refund);
    }

    /**
     * @notice 学生辞职
     * @param _employmentId 任职合约ID
     */
    function resignFromJob(uint256 _employmentId) external onlyStudent(_employmentId) {
        Employment storage emp = employments[_employmentId];
        require(emp.id != 0, "Employment does not exist");
        require(
            emp.status == EmploymentStatus.APPROVED || emp.status == EmploymentStatus.PENDING,
            "Cannot resign from this employment"
        );

        emp.status = EmploymentStatus.REJECTED; // 复用REJECTED状态表示离职

        // 如果岗位已被填充，重新开放
        Job storage job = jobs[emp.jobId];
        if (job.status == JobStatus.FILLED) {
            job.status = JobStatus.OPEN;
        }

        emit StudentResigned(_employmentId, msg.sender);
    }

    // ======================== 查询函数 ========================

    function getJob(uint256 _jobId) external view returns (Job memory) {
        return jobs[_jobId];
    }

    function getEmployment(uint256 _employmentId) external view returns (Employment memory) {
        return employments[_employmentId];
    }

    function getClockIn(uint256 _clockInId) external view returns (ClockInRecord memory) {
        return clockIns[_clockInId];
    }

    function getSettlement(uint256 _settlementId) external view returns (Settlement memory) {
        return settlements[_settlementId];
    }

    // 获取合约余额
    function getContractBalance() external view returns (uint256) {
        return address(this).balance;
    }

    // 获取结算配置
    function getSettlementConfig() external view returns (
        uint256 cycleDays,
        uint256 lastSettlementTime,
        bool autoEnabled,
        uint256 gracePeriodHours,
        uint256 nextSettlementTime
    ) {
        SettlementConfig storage c = settlementConfig;
        return (
            c.cycleDays,
            c.lastSettlementTime,
            c.autoEnabled,
            c.gracePeriodHours,
            c.lastSettlementTime + c.cycleDays * 1 days
        );
    }
}
