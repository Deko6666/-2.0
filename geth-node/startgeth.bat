@echo off
geth.exe --db.engine=pebble --syncmode full --networkid 557766 --datadir dev-chain --dev --http --http.port=8545 --http.addr="0.0.0.0" --graphql --http.api admin,debug,web3,eth,txpool,miner,net,dev --http.corsdomain="*" --allow-insecure-unlock --unlock=0,1,2,3,4,5,6 --password passwd.txt --ws --ws.addr "0.0.0.0" --ws.port=8546 --vmdebug --dev.period 0
pause
