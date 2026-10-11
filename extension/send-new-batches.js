// Send exactly the reviewed selection, sequentially; never retry an uncertain batch.
export async function sendNewBatches(body,sendBatch){
  const results=[];
  for(let start=0;start<body.items.length;start+=20){
    const items=body.items.slice(start,start+20);
    let data;
    try{data=await sendBatch({...body,items});}
    catch(error){return {results,message:`Отправка остановлена: ${error.message}. Подтверждено: ${results.filter(r=>r.state==='sent').length}. Проверьте результат отправки перед продолжением.`};}
    results.push(...data.results);
    if(data.results.length!==items.length||data.results.some(r=>r.state!=='sent'))return {results,message:`Отправка остановлена: результат части операций не подтверждён. Проверьте результат отправки. Подтверждено: ${results.filter(r=>r.state==='sent').length} из ${body.items.length}.`};
  }
  return {results};
}
