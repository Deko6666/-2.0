// 04b_replace_tx.js ── 用相同 nonce + 更高 gasPrice 替换卡住的交易
const { ethers } = require('ethers');
const fs = require('fs');

// ==================== 配置区 ====================
const PRIVATE_KEY = '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80';
const TO_ADDRESS  = '0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266';
const RPC_URL     = 'http://localhost:8545';
const MY_CHAIN_ID = 887766n; 
// ===============================================

async function main() {
  const provider = new ethers.JsonRpcProvider(RPC_URL);
  const wallet   = new ethers.Wallet(PRIVATE_KEY, provider);

  // 从文件中读取刚才卡住的那个 nonce
  if (!fs.existsSync('stuck_nonce.txt')) {
      console.error("找不到 stuck_nonce.txt 文件，请先运行 04a 脚本！");
      return;
  }
  const stuckNonce = parseInt(fs.readFileSync('stuck_nonce.txt', 'utf8'));
  console.log('要替换的 nonce:', stuckNonce);

  // 新交易：相同 nonce 1，但 gasPrice 提高到 2 gwei (之前是 1 gwei)
  const tx = {
    to:       TO_ADDRESS,
    value:    ethers.parseEther('0.002'), // 可以顺便改一下金额方便观察
    nonce:    stuckNonce,                 // ← 必须和卡住的交易 nonce 相同！
    gasLimit: 21000,
    gasPrice: ethers.parseUnits('2', 'gwei'), // ← 提高 Gas 费进行加速/替换
    chainId:  MY_CHAIN_ID,
  };

  console.log('正在广播替换交易...');
  
  try {
    const signedTx = await wallet.signTransaction(tx);
    const txResp   = await provider.broadcastTransaction(signedTx);
    
    console.log('✅ 替换交易已广播！新的 TxHash:', txResp.hash);
    console.log('nonce =', stuckNonce, '，新的 gasPrice = 2 gwei');
    console.log('💡 原来的低 gasPrice 交易已被驱逐出 mempool。');
  } catch (error) {
    console.error('❌ 替换失败:', error.message);
  }
}

main().catch(console.error);
