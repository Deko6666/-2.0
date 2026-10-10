const fs = require("fs");
const path = require("path");

const RPC_URL = "http://127.0.0.1:8545";

async function rpc(method, params = []) {
  const res = await fetch(RPC_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", method, params, id: Date.now() }),
  });
  const data = await res.json();
  if (data.error) throw new Error(data.error.message);
  return data.result;
}

async function sendTx(txObj) {
  const txHash = await rpc("eth_sendTransaction", [txObj]);
  let receipt = null;
  for (let i = 0; i < 60; i++) {
    receipt = await rpc("eth_getTransactionReceipt", [txHash]);
    if (receipt) return receipt;
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("交易确认超时: " + txHash);
}

async function main() {
  console.log("🚀 正在部署 WorkStudy 合约到 Geth Dev Chain...");

  const accounts = await rpc("eth_accounts");
  if (!accounts || accounts.length === 0) {
    throw new Error("Geth没有可用账户！");
  }
  const deployer = accounts[0];
  console.log(`📋 部署账户: ${deployer}`);

  const balanceHex = await rpc("eth_getBalance", [deployer, "latest"]);
  const balance = BigInt(balanceHex);
  console.log(`💰 账户余额: ${(Number(balance) / 1e18).toFixed(2)} ETH`);

  // 直接读编译产物
  const artifactPath = path.join(__dirname, "..", "artifacts", "contracts", "WorkStudy.sol", "WorkStudy.json");
  const artifact = JSON.parse(fs.readFileSync(artifactPath, "utf8"));
  const deployData = artifact.bytecode;
  if (!deployData || deployData === "0x") throw new Error("bytecode为空，请先运行 npx hardhat compile");

  console.log(`📦 字节码长度: ${deployData.length} chars`);

  // 估算部署所需 gas（WorkStudy 字节码大，旧版固定 4M 会导致 out-of-gas 部署失败）
  let gasLimit = "0x989680"; // 10M 兜底
  try {
    const est = await rpc("eth_estimateGas", [{ from: deployer, data: deployData }]);
    gasLimit = "0x" + Math.ceil(parseInt(est, 16) * 1.3).toString(16);
    console.log(`⛽ 预估 gas: ${parseInt(est, 16)}，实际使用: ${parseInt(gasLimit, 16)}`);
  } catch (e) {
    console.log(`⛽ gas 估算失败，使用兜底值 10,000,000`);
  }

  // 部署
  console.log("⏳ 发送部署交易...");
  const deployReceipt = await sendTx({
    from: deployer,
    data: deployData,
    gas: gasLimit,
  });

  // 关键：必须检查 status！Geth 对失败的部署也会返回 contractAddress
  if (deployReceipt.status !== "0x1") {
    throw new Error(`部署交易执行失败 (status=${deployReceipt.status})，gasUsed=${parseInt(deployReceipt.gasUsed, 16)}。请调高 gas 上限`);
  }

  const address = deployReceipt.contractAddress;
  if (!address) throw new Error("部署失败：未返回合约地址");

  // 再确认地址上确实有合约代码
  const code = await rpc("eth_getCode", [address, "latest"]);
  if (!code || code === "0x" || code === "0x0") {
    throw new Error(`部署异常：地址 ${address} 上没有合约代码！`);
  }
  console.log(`✅ 合约部署成功！`);
  console.log(`📍 合约地址: ${address}`);
  console.log(`📏 合约代码: ${(code.length - 2) / 2} bytes`);

  // 更新前端合约地址
  const indexPath = path.join(__dirname, "..", "frontend", "index.html");
  if (fs.existsSync(indexPath)) {
    let content = fs.readFileSync(indexPath, "utf8");
    content = content.replace(
      /const CONTRACT_ADDR = "0x[a-fA-F0-9]{40}"/,
      `const CONTRACT_ADDR = "${address}"`
    );
    fs.writeFileSync(indexPath, content, "utf8");
    console.log(`✅ 前端合约地址已自动更新`);
  }

  // 编码 registerAsTeacher 调用
  const iface = new (require("ethers").Interface)(artifact.abi);
  const registerData = iface.encodeFunctionData("registerAsTeacher");

  console.log("⏳ 注册部署者为教师...");
  try {
    const regReceipt = await sendTx({
      from: deployer,
      to: address,
      data: registerData,
      gas: "0x493E0",
    });
    if (regReceipt.status === "0x1") {
      console.log(`✅ 部署者 ${deployer} 已注册为教师`);
    } else {
      console.log(`⚠️ 教师注册可能已存在`);
    }
  } catch (e) {
    console.log(`⚠️ 教师注册跳过: ${e.message}`);
  }

  // 保存合约地址到配置文件供后端读取
  const configPath = path.join(__dirname, "..", "contract_address.txt");
  fs.writeFileSync(configPath, address, "utf8");
  console.log(`✅ 合约地址已保存到 contract_address.txt（供后端自动结算使用）`);

  const chainId = await rpc("eth_chainId");
  console.log(`\n📋 部署信息汇总:`);
  console.log(`   合约地址: ${address}`);
  console.log(`   部署者:   ${deployer}`);
  console.log(`   网络:     Geth Dev Chain`);
  console.log(`   Chain ID: ${parseInt(chainId, 16)}`);
  console.log(`   自动结算: ✅ 已启用（默认30天周期）`);
}

main().catch((error) => {
  console.error("❌ 部署失败:", error);
  process.exitCode = 1;
});
