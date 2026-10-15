@echo off
cd /d "%~dp0"
rem ============================================================
rem  Campus Work-Study chain: Clique PoA, chainId 887766 (0xD8BD6)
rem  Matches the MetaMask network "Geth-Dey" (RPC 127.0.0.1:8545)
rem  NOTE: geth 1.15+ dropped legacy consensus engines, so this
rem  project bundles Geth 1.13.15 (geth-1.13\geth.exe).
rem  First run only: geth-1.13\geth.exe --datadir chain-887766 init genesis-887766.json
rem ============================================================
geth-1.13\geth.exe --db.engine=pebble --syncmode full --networkid 557766 --datadir chain-887766 --http --http.port=8545 --http.addr="0.0.0.0" --graphql --http.api admin,debug,web3,eth,txpool,miner,net --http.corsdomain="*" --http.vhosts="*" --allow-insecure-unlock --unlock=0 --password passwd.txt --mine --miner.etherbase 0x3958540750b8ee267619d681bd5c7f70098193aa --ws --ws.addr "0.0.0.0" --ws.port=8546 --ws.origins="*" --vmdebug
pause
