// 04d_clear_gap.js ── 专门用来发出的 Nonce 0 交易，疏通后续所有排队的交易
const { ethers } = require('ethers');

// ==================== 配置区 ====================
const PRIVATE_KEY = '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80'; // 你的私钥
const RPC_URL     = 'http://localhost:8545';
const MY_CHAIN_ID = 887766n; 
// ===============================================

async function main() {
  const provider = new ethers.JsonRpcProvider(RPC_URL);
  const wallet   = new ethers.Wallet(PRIVATE_KEY, provider);

  console.log('正在为地址:', wallet.address, '发送 Nonce 0 的补位交易...');

  const tx = {
    to:       wallet.address,  // 发给自己就行
    value:    0n,              // 0 元交易
    nonce:    0,               // ← 关键：填补最开始那个 Nonce 0 的空洞
    gasLimit: 21000,
    gasPrice: ethers.parseUnits('2', 'gwei'),
    chainId:  MY_CHAIN_ID,
  };

  try {
    const txResp = await wallet.sendTransaction(tx);
    console.log('✅ Nonce 0 交易已广播！TxHash:', txResp.hash);
    console.log('等待上链中...');
    
    await txResp.wait();
    console.log('🚀 Nonce 0 已成功上链！这会立刻触发后续 Nonce 1 的自动打包。');
    
    // 检查最新的 nonce
    const latestNonce = await provider.getTransactionCount(wallet.address, 'latest');
    console.log('该地址当前已完成的 Nonce 总数:', latestNonce);
  } catch (error) {
    console.error('❌ 发送失败:', error.message);
  }
}

main().catch(console.error);
