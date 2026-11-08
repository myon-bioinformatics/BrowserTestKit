// No runtime dependency on import. The CLI uses the consumer's existing Playwright.
import {isIP} from 'node:net';
import {readFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import {createRequire} from 'node:module';
import {resolve} from 'node:path';
const receipt=(steps,total,error)=>({schema:'route-trail-run/1',ok:!error,total,completed:steps.length,steps,...(error?{error}:{})});
// Shared with route_trail.py EXECUTION_URL_SOURCE, character for character (see the comment there).
export const EXECUTION_URL = /^(?:([Hh][Tt][Tt][Pp][Ss]?):\/\/([A-Za-z0-9.-]+|\[[0-9A-Fa-f:.]+\])(?::([0-9]{1,5}))?(?:[/?#][^\x00-\x20\x7f-\x9f\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff\\]*)?|[Ff][Ii][Ll][Ee]:\/\/(?:[Ll][Oo][Cc][Aa][Ll][Hh][Oo][Ss][Tt])?\/[^\x00-\x20\x7f-\x9f\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff\\]*)$/;
function executionUrl(url){
  // JS $ permits a final newline: require the entire match as Python fullmatch does.
  const match=EXECUTION_URL.exec(url);
  if(!match||match[0]!==url)return false;
  const [,scheme,host,port]=match;
  if(scheme===undefined)return true;
  if(port!==undefined&&(Number(port)<1||Number(port)>65535))return false;
  if(host.startsWith('['))return isIP(host.slice(1,-1))===6;
  const dns=host.endsWith('.')?host.slice(0,-1):host;
  if(!dns||dns.length>253)return false;
  const labels=dns.toLowerCase().split('.');
  if(labels.some(label=>!(/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/).test(label)||label.startsWith('xn--')))return false;
  if((/^(?:[0-9]+|0x[0-9a-f]*)$/).test(labels.at(-1))){
    return labels.length===4&&labels.every(label=>(/^(?:0|[1-9][0-9]{0,2})$/).test(label)&&Number(label)<=255);
  }
  return true;
}

export class TrailExecutionError extends Error {
  constructor(result){super(result.error.message);this.receipt=result;}
}
export function validateSteps(steps,timeoutMs=10000){
  if(!Number.isInteger(timeoutMs)||timeoutMs<1||timeoutMs>120000)throw Error('invalid timeout_ms');
  if(!Array.isArray(steps)||steps.length<1||steps.length>1000)throw Error('steps must contain 1..1000 records');
  for(const step of steps){
    if(!step||typeof step!=='object'||Array.isArray(step)||Object.keys(step).some(k=>!['id','emoji','route','url','title','ready'].includes(k)))throw Error('invalid execution step');
    if(['emoji','route','url'].some(k=>typeof step[k]!=='string'))throw Error('step requires emoji, route and url strings');
    // Same rule as route_trail.py EXECUTION_URL; WHATWG URL would repair inputs urlsplit rejects.
    if(Array.from(step.url).length>4096||!executionUrl(step.url))throw Error('execution URLs must be absolute HTTP(S) or local file URLs without credentials');
    if(step.ready!=null){
      const r=step.ready;
      if(typeof r!=='object'||Array.isArray(r)||Object.keys(r).some(k=>!['selector','text'].includes(k))||typeof r.selector!=='string'||!r.selector||r.selector.length>512)throw Error('invalid ready selector');
      if('text' in r && (typeof r.text!=='string'||r.text.length>2000))throw Error('invalid ready text');
    }
  }
  return steps;
}
const call=async(fn,page,step)=>{if(await fn(page,structuredClone(step))===false)throw Error('hook returned False');};
export async function* walkSteps(page,steps,{ready,check,navigate,timeoutMs=10000}={}){
  validateSteps(steps,timeoutMs);
  steps=structuredClone(steps);
  const done=[];
  for(let index=0;index<steps.length;index++){
    const step=steps[index],hookStep={...step,index};let phase='navigate';let record;
    try {
      if(navigate)await call(navigate,page,hookStep);
      else {const response=await page.goto(step.url,{waitUntil:'domcontentloaded',timeout:timeoutMs});if(response && response.status()>=400)throw Error('HTTP status '+response.status());}
      phase='ready';
      if(step.ready){
        const target=page.locator(step.ready.selector);await target.waitFor({state:'visible',timeout:timeoutMs});
        if('text' in step.ready){
          const words=step.ready.text.trim().split(/\s+/u).map(word=>word.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'));
          const exact=new RegExp('^\\s*'+words.join('\\s+')+'\\s*$','u');
          await target.filter({hasText:exact}).waitFor({state:'visible',timeout:timeoutMs});
        }
      }
      if(ready)await call(ready,page,hookStep);
      phase='verify';await page.waitForURL(url=>url.href===step.url,{timeout:timeoutMs});
      if(check)await call(check,page,hookStep);
      record={...step,index,status:'passed',verification:step.ready?'url+marker':'url',observed_url:page.url()};done.push(record);
    } catch(e){throw new TrailExecutionError(receipt([...done],steps.length,{index,route:step.route,phase,message:String(e.message||e).slice(0,2000),type:e.name||'Error'}));}
    yield structuredClone(record); // consumer may do other work before asking for the next step
  }
}
export async function walk(page,steps,options={}){
  const total=steps.length;const done=[];for await(const step of walkSteps(page,steps,options))done.push(step);
  return receipt(done,total);
}
async function main(){
  let request,result,browser;
  try {
    const raw=readFileSync(0,'utf8');if(Buffer.byteLength(raw)>2*1024*1024)throw Error('execution plan exceeds 2 MiB');
    request=JSON.parse(raw);validateSteps(request.steps,request.timeout_ms);
    if(!['chromium','firefox','webkit'].includes(request.browser))throw Error('invalid browser');
    const require=createRequire(resolve(process.cwd(),'package.json'));
    let pw;
    try{pw=require('playwright');}
    catch(e){
      if(e.code!=='MODULE_NOT_FOUND'||!e.message.startsWith("Cannot find module 'playwright'"))throw e;
      pw=require('@playwright/test');
    }
    browser=await pw[request.browser].launch();
    const page=await browser.newPage();result=await walk(page,request.steps,{timeoutMs:request.timeout_ms});
  } catch(e){result=e instanceof TrailExecutionError?e.receipt:receipt([],Array.isArray(request?.steps)?request.steps.length:0,{phase:'setup',message:String(e.message||e).slice(0,2000),type:e.name||'Error'});}
  finally {if(browser){try{await browser.close();}catch(e){const detail=String(e.message||e).slice(0,2000);if(result?.ok){result.ok=false;result.error={phase:'cleanup',message:detail};}else if(result)result.cleanup_error=detail;}}}
  process.stdout.write(JSON.stringify(result)+'\n');process.exitCode=result.ok?0:1;
}
if(process.argv[1] && import.meta.url===pathToFileURL(process.argv[1]).href)await main();
