"""Explicit transport-failure continuation. Original run stays immutable."""
import asyncio
from time import perf_counter
import json
import run_ablation_v1 as run


async def main():
    run.locked()
    run.setup()
    source=run.HERE/'answers-fixed-v1.json'
    original=run.read(source)
    report=run.read(run.HERE/'retrieval-v1.json')
    destination=run.HERE/'answers-fixed-completed-v1.json'
    if destination.exists(): raise FileExistsError(destination)
    output={'metadata':{'started_at':run.now(),'original_sha256':run.sha(source),
                         'resume_script_sha256':run.sha(__import__('pathlib').Path(__file__)),
                         'amendment':'Resume missing items, retry original APIConnectionError once explicitly. Original failures retained; no completed answer rerun; no new prompts/parameters.',
                         'usage_warning':'Original disconnected call has unknown usage, not zero. Complete-pair semantic analysis is supplementary to original first-attempt execution result.'},
            'original_failure_records':[], 'rows':[]}
    existing={r['id']:r for r in original['rows']}
    model=run.client()
    from langchain_core.messages import SystemMessage,HumanMessage
    prompt=(run.HERE/'fixed-answer-prompt-v1.txt').read_text(encoding='utf-8')
    run.save(destination,output)
    for i,row in enumerate(report['rows']):
        prior=existing.get(row['id'],{}).get('answers',{})
        result={'id':row['id'],'answers':{}}
        output['rows'].append(result)
        for arm in (['off','on'] if i%2==0 else ['on','off']):
            if 'answer' in prior.get(arm,{}):
                result['answers'][arm]=prior[arm]
                continue
            if arm in prior:
                output['original_failure_records'].append({'id':row['id'],'arm':arm,**prior[arm]})
            started=perf_counter()
            try:
                response=await model.ainvoke([SystemMessage(content=prompt),HumanMessage(content=json.dumps({'question':row['question'],'evidence':row[arm]['evidence']},ensure_ascii=False))])
                result['answers'][arm]={'answer':response.content,'usage':response.usage_metadata or {},
                                         'model':response.response_metadata.get('model_name'),
                                         'finish_reason':response.response_metadata.get('finish_reason'),
                                         'generation_ms':(perf_counter()-started)*1000,
                                         'continuation':True,'explicit_retry':arm in prior}
            except Exception as exc:
                result['answers'][arm]={'error_type':type(exc).__name__,'generation_ms':(perf_counter()-started)*1000,
                                         'continuation':True,'explicit_retry':arm in prior}
            run.save(destination,output)
            print(row['id'],arm,'answer' if 'answer' in result['answers'][arm] else 'error',flush=True)
    output['metadata']['completed_at']=run.now()
    run.save(destination,output)


if __name__=='__main__': asyncio.run(main())
