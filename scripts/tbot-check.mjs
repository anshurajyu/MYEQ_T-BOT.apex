/** Offline checks only: no serial devices or ROS services are opened. */
import {spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
const python=process.env.TBOT_TEST_PYTHON || (process.platform==='win32'?'.venv-tbot/Scripts/python.exe':'.venv-tbot/bin/python');
const checks=[
 [process.execPath,['scripts/tbot-input-tests.mjs']],
 [process.execPath,['--test','scripts/tbot-tablet-tests.mjs','scripts/tbot-voice-tests.mjs']],
 [python,['-m','pytest','backend/tests','-q','-p','no:cacheprovider']],
 [process.execPath,['node_modules/typescript/bin/tsc','--noEmit','--incremental','false']],
];
if(!existsSync(python)&&!process.env.TBOT_TEST_PYTHON)throw Error('Install backend/requirements.txt in .venv-tbot or set TBOT_TEST_PYTHON.');
for(const [command,args] of checks){
 console.log(`\nRunning ${args.join(' ')}`);
 const result=spawnSync(command,args,{stdio:'inherit'});
 if(result.error)throw result.error;
 if(result.status!==0)process.exit(result.status??1);
}
console.log('\nAll offline control checks passed. Hardware acceptance still requires the robot.');
