import {initializeApp,getApps} from 'firebase/app';
import {getAuth,GoogleAuthProvider,signInWithPopup,signOut} from 'firebase/auth';
import {getFirestore,doc,getDoc,setDoc,collection,getDocs,serverTimestamp} from 'firebase/firestore';
import type {Library,Mission} from './client';
export async function syncCloud(config:Record<string,string>,library:Library){
 if(!config.apiKey||!config.projectId||!config.authDomain||!config.teamId)throw Error('Enter Firebase web configuration and team ID first');
 if(!/^[a-zA-Z0-9_-]{1,80}$/.test(config.teamId))throw Error('Invalid team ID');
 const app=getApps().find(a=>a.name===config.projectId)??initializeApp(config,config.projectId),auth=getAuth(app);
 const user=auth.currentUser??(await signInWithPopup(auth,new GoogleAuthProvider())).user;
 const db=getFirestore(app),member=await getDoc(doc(db,'teams',config.teamId,'members',user.uid));if(!member.exists())throw Error('This account is not an authorized team member');
 for(const mission of library.missions){const {id,...body}=mission;if(id)await setDoc(doc(db,'teams',config.teamId,'missions',id),{...body,updatedAt:serverTimestamp()})}
 for(const run of library.runs.slice(0,50)){if(run.id)await setDoc(doc(db,'teams',config.teamId,'runs',String(run.id)),{...run,updatedAt:serverTimestamp()})}
 await setDoc(doc(db,'teams',config.teamId,'preferences',user.uid),{...library.settings,updatedAt:serverTimestamp()});
 const remote=await getDocs(collection(db,'teams',config.teamId,'missions'));
 return {email:user.email,missions:remote.docs.map(d=>({id:d.id,...d.data()} as Mission))};
}
export async function signOutCloud(projectId:string){const app=getApps().find(a=>a.name===projectId);if(app)await signOut(getAuth(app))}
