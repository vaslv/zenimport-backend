// Explicit diagnostics only. Values are denied by default; no headers/cookies.
export function describeHistoryRequest(request) {
  const fields=(value,key='',depth=0)=>{
    if(depth>6)return {type:'truncated'};
    if(value===null)return {type:'null'};
    if(Array.isArray(value))return {type:'array',length:value.length,item:value.length?fields(value[0],'',depth+1):null};
    if(typeof value==='object')return {type:'object',fields:Object.fromEntries(Object.entries(value).slice(0,100).map(([k,v],i)=>[/^[A-Za-z_][A-Za-z0-9_.-]{0,63}$/.test(k)?k:`field_${i}`,fields(v,k,depth+1)]))};
    const type=typeof value;
    if(/^(page|currentPage|nextPage|pageSize|offset|limit|pageIndex|pageNumber)$/i.test(key)&&/^[0-9]{1,6}$/.test(String(value)))return {type,value};
    if(/^(fromDate|toDate|startDate|endDate|dateFrom|dateTo|from|to|start|end)$/i.test(key)&&typeof value==='string'&&(/^[0-9]{4}[-/][0-9]{2}[-/][0-9]{2}$/.test(value)||/^[0-9]{2}[-/][0-9]{2}[-/][0-9]{4}$/.test(value)))return {type,value};
    return {type};
  };
  const url=new URL(request.url);
  if(url.origin+url.pathname!=='https://api-ipay.vietinbank.vn/ipay/wa/getHistTransactions')throw Error('Unsupported endpoint');
  let body={type:'absent'};
  const post=request.postData;
  if(post?.text&&post.text.length<=200000){
    try{body=fields(JSON.parse(post.text));}
    catch {if(post.mimeType?.split(';')[0]==='application/x-www-form-urlencoded')body=fields(Object.fromEntries(new URLSearchParams(post.text)));else body={type:'unparsed'};}
  }else if(Array.isArray(post?.params))body=fields(Object.fromEntries(post.params.map(p=>[p.name,p.value])));
  return {version:1,endpoint:url.origin+url.pathname,method:['GET','POST'].includes(request.method)?request.method:'other',query:fields(Object.fromEntries(url.searchParams)),body};
}
