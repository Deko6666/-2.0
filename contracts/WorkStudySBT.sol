// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/**
 * @dev WorkStudy 合约的教师注册表接口（public mapping isTeacher 的 getter）
 */
interface IWorkStudy {
    function isTeacher(address account) external view returns (bool);
}

/**
 * @title WorkStudySBT - 勤工助学履约勋章（ERC-5192 Soulbound Token）
 * @notice 不可转让的链上履约证书，绑定学生钱包地址
 * @dev 遵循ERC-5192规范（Locked Soulbound Token）
 */
contract WorkStudySBT {

    string public name = "WorkStudy Badge";
    string public symbol = "WSB";

    mapping(uint256 => address) private _owners;
    mapping(address => uint256) private _balances;
    uint256 private _totalSupply;

    // ERC-5192: 锁定状态（不可转让）
    mapping(uint256 => bool) private _locked;

    // ============ 勋章元数据 ============

    struct BadgeMetadata {
        uint256 tokenId;
        address student;
        string jobTitle;
        string department;
        uint256 totalHours;      // 累计工时(分钟)
        string teacherComment;   // 教师评语
        uint8 starRating;        // 星级评分(1-5)
        uint256 issuedAt;        // 颁发时间
        bool revoked;            // 是否被撤销
    }

    mapping(uint256 => BadgeMetadata) public badges;
    mapping(address => uint256[]) private _studentBadges;
    uint256 public badgeCount;

    address public admin;
    address public workStudyContract;

    // ============ 事件 ============

    event Transfer(address indexed from, address indexed to, uint256 indexed tokenId);
    event BadgeIssued(uint256 indexed tokenId, address indexed student, string jobTitle, uint256 totalHours);
    event BadgeRevoked(uint256 indexed tokenId, address indexed student);

    // ============ 修饰符 ============

    modifier onlyAdmin() {
        require(msg.sender == admin, "Only admin");
        _;
    }

    modifier onlyAuthorized() {
        require(_canIssue(msg.sender), "Unauthorized");
        _;
    }

    // ============ 构造函数 ============

    constructor(address _workStudy) {
        admin = msg.sender;
        workStudyContract = _workStudy;
    }

    // ============ ERC-721 核心（精简实现） ============

    function balanceOf(address owner) public view returns (uint256) {
        require(owner != address(0), "Zero address");
        return _balances[owner];
    }

    function ownerOf(uint256 tokenId) public view returns (address) {
        address owner = _owners[tokenId];
        require(owner != address(0), "Nonexistent token");
        return owner;
    }

    // 禁止一切转移操作 — ERC-5192 灵魂绑定
    function transferFrom(address, address, uint256) public pure {
        revert("Soulbound: non-transferable");
    }

    function safeTransferFrom(address, address, uint256) public pure {
        revert("Soulbound: non-transferable");
    }

    function safeTransferFrom(address, address, uint256, bytes memory) public pure {
        revert("Soulbound: non-transferable");
    }

    // ERC-5192: 查询锁定状态
    function locked(uint256 tokenId) external view returns (bool) {
        require(_owners[tokenId] != address(0), "Nonexistent token");
        return _locked[tokenId];
    }

    function supportsInterface(bytes4 interfaceId) public pure returns (bool) {
        return interfaceId == bytes4(keccak256("supportsInterface(bytes4)")) ||
               interfaceId == 0x80ac58cd ||  // ERC-721
               interfaceId == 0xb45a3c0e;    // ERC-5192
    }

    // ============ 内部铸造/销毁 ============

    function _safeMint(address to, uint256 tokenId) internal {
        require(to != address(0), "Mint to zero");
        require(_owners[tokenId] == address(0), "Already exists");
        _owners[tokenId] = to;
        _balances[to] += 1;
        _totalSupply++;
        emit Transfer(address(0), to, tokenId);
    }

    // ============ 业务逻辑 ============

    /**
     * @dev 颁发权限：SBT 管理员 / WorkStudy 合约 / WorkStudy 注册教师
     */
    function _canIssue(address account) internal view returns (bool) {
        return account == admin || account == workStudyContract || _isRegisteredTeacher(account);
    }

    /**
     * @dev 查询 WorkStudy 合约上的教师注册表；合约缺失或调用失败时返回 false
     */
    function _isRegisteredTeacher(address account) internal view returns (bool) {
        if (workStudyContract == address(0)) return false;
        try IWorkStudy(workStudyContract).isTeacher(account) returns (bool ok) {
            return ok;
        } catch {
            return false;
        }
    }

    /**
     * @notice 公开查询某地址是否具备颁发权限（前端预检 / 验真用）
     */
    function canIssue(address account) external view returns (bool) {
        return _canIssue(account);
    }

    function issueBadge(
        address _student,
        string memory _jobTitle,
        string memory _department,
        uint256 _totalHours,
        string memory _teacherComment,
        uint8 _starRating
    ) external onlyAuthorized returns (uint256) {
        require(_starRating >= 1 && _starRating <= 5, "Rating must be 1-5");

        badgeCount++;
        uint256 tokenId = badgeCount;

        _safeMint(_student, tokenId);
        _locked[tokenId] = true;  // 铸造后立即锁定，不可转让

        badges[tokenId] = BadgeMetadata({
            tokenId: tokenId,
            student: _student,
            jobTitle: _jobTitle,
            department: _department,
            totalHours: _totalHours,
            teacherComment: _teacherComment,
            starRating: _starRating,
            issuedAt: block.timestamp,
            revoked: false
        });

        _studentBadges[_student].push(tokenId);
        emit BadgeIssued(tokenId, _student, _jobTitle, _totalHours);
        return tokenId;
    }

    function getBadge(uint256 tokenId) external view returns (BadgeMetadata memory) {
        require(_owners[tokenId] != address(0), "Nonexistent badge");
        return badges[tokenId];
    }

    function getStudentBadges(address _student) external view returns (uint256[] memory) {
        return _studentBadges[_student];
    }

    function totalSupply() external view returns (uint256) {
        return _totalSupply;
    }

    function revokeBadge(uint256 tokenId) external onlyAdmin {
        require(_owners[tokenId] != address(0), "Nonexistent badge");
        badges[tokenId].revoked = true;
        emit BadgeRevoked(tokenId, badges[tokenId].student);
    }
}
