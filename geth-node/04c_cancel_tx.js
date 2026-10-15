// 04c_cancel_tx.js ── 取消卡住的交易（通过发送 0 元空交易抢占 Nonce）
const { ethers } = require('ethers');
const fs = require('fs');

// ==================== 配置区 ====================
// 必须使用和你 04a 脚本相同的私钥
const PRIVATE_KEY = '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80';
const RPC_URL     = 'http://localhost:8545';
const MY_CHAIN_ID = 887766n; 
// ===============================================

async function main() {
  const provider = new ethers.JsonRpcProvider(RPC_URL);
  const wallet   = new ethers.Wallet(PRIVATE_KEY, provider);

  // 从文件中读取那个被卡住的 nonce（比如 1）
  const stuckNonce = parseInt(fs.readFileSync('stuck_nonce.txt', 'utf8'));
  console.log('准备取消 nonce 为', stuckNonce, '的交易...');

  const cancelTx = {
    to:       wallet.address,  // ← 【关键点 1】接收方改为自己
    value:    0n,              // ← 【关键点 2】转账金额改为 0
    nonce:    stuckNonce,      // ← 【关键点 3】必须是同一个 nonce
    gasLimit: 21000,
    // 【关键点 4】GasPrice 必须比之前那笔（你刚才 04b 改成了 2 gwei）更高
    // 这里我们给 5 gwei 确保成功替换
    gasPrice: ethers.parseUnits('5', 'gwei'), 
    chainId:  MY_CHAIN_ID,
  };

  console.log('正在广播取消交易...');
  
  try {
    const signedTx = await wallet.signTransaction(cancelTx);
    const txResp   = await provider.broadcastTransaction(signedTx);
    
    console.log('✅ 取消交易已广播！TxHash:', txResp.hash);
    console.log('💡 结果：原来的交易已被驱逐。现在 nonce', stuckNonce, '对应的操作是“发给自己 0 元”。');
  } catch (error) {
    console.error('❌ 取消失败:', error.message);
  }
}

main().catch(console.error);
