
// 04a_stuck_tx.js ── 发出一笔故意卡住的交易（nonce 空洞方式）
const { ethers } = require('ethers');
const fs = require('fs');

const PRIVATE_KEY = '0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80';
const TO_ADDRESS  = '0x70997970C51812dc3A010C7d01b50e0d17dc79C8';
const RPC_URL     = 'http://localhost:8545';

async function main() {
  const provider = new ethers.JsonRpcProvider(RPC_URL);
  const wallet   = new ethers.Wallet(PRIVATE_KEY, provider);

  const latestNonce = await provider.getTransactionCount(wallet.address, 'latest');
  console.log('当前已上链 nonce:', latestNonce);

  // 故意用 nonce+1，跳过当前 nonce，交易永远无法被打包
  const stuckNonce = latestNonce + 1;

  const tx = {
    to:       TO_ADDRESS,
    value:    ethers.parseEther('0.001'),
    nonce:    stuckNonce,   // ← 跳空！latestNonce 那笔从未发出
    gasLimit: 21000,
    gasPrice: ethers.parseUnits('1', 'gwei'),
    chainId:  887766n,      // ← eth.chainId() 返回的十进制值
  };

  const signedTx = await wallet.signTransaction(tx);
  const txResp   = await provider.broadcastTransaction(signedTx);
  console.log('已广播（卡住的）TxHash:', txResp.hash);
  console.log('这笔交易 nonce =', stuckNonce, '（跳空，永远不会上链）');

  fs.writeFileSync('stuck_nonce.txt', stuckNonce.toString());
  console.log('nonce 已保存到 stuck_nonce.txt');
}

main().catch(console.error);

