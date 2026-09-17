import {readFileSync} from 'node:fs';
import {initializeTestEnvironment,assertFails,assertSucceeds} from '@firebase/rules-unit-testing';
import {doc,setDoc,getDoc} from 'firebase/firestore';
const env=await initializeTestEnvironment({projectId:'demo-tbot',firestore:{host:'127.0.0.1',port:8080,rules:readFileSync('firebase/firestore.rules','utf8')}});
try{
 await env.clearFirestore();
 await env.withSecurityRulesDisabled(async context=>{await setDoc(doc(context.firestore(),'teams/lab/members/member'),{role:'operator'})});
 const member=env.authenticatedContext('member',{email_verified:true}).firestore(),outsider=env.authenticatedContext('outsider',{email_verified:true}).firestore(),guest=env.unauthenticatedContext().firestore();
 const path='teams/lab/missions/route',mission={name:'Lab',map_id:'map',map_version:'0123456789abcdef',points:[{x:1,y:1,yaw:0}],return_home:true};
 await assertSucceeds(setDoc(doc(member,path),mission));
 await assertSucceeds(getDoc(doc(member,path)));
 await assertFails(getDoc(doc(outsider,path)));await assertFails(getDoc(doc(guest,path)));
 await assertFails(setDoc(doc(outsider,'teams/lab/members/outsider'),{role:'operator'}));
 await assertFails(setDoc(doc(member,path),{...mission,points:[]}));
 await assertFails(setDoc(doc(member,path),{...mission,command:'drive'}));
 await assertFails(setDoc(doc(member,'teams/other/missions/route'),mission));
 await assertFails(getDoc(doc(env.authenticatedContext('member',{email_verified:false}).firestore(),path)));
 await assertSucceeds(setDoc(doc(member,'teams/lab/preferences/member'),{teamMode:true}));
 await assertFails(setDoc(doc(member,'teams/lab/preferences/other'),{teamMode:true}));
 console.log('11 Firebase authorization and validation checks passed.');
}finally{await env.cleanup()}
