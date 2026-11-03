"""Close only tabs explicitly created by a task, retaining human checkpoints."""
HUMAN_CHECKPOINTS={'LOGIN_REQUIRED','LOGIN_UNKNOWN','CHALLENGE','ACCESS_DENIED'}


def close_owned_tabs(browser,tabs,pause_reason=None):
    owned={t.tab_id:t for t in tabs}
    result={'owned':list(owned),'closed':[],'retained':[],'errors':[]}
    if pause_reason in HUMAN_CHECKPOINTS:
        result['retained']=list(owned)
        result['reason']=pause_reason
        return result
    for tab_id,tab in reversed(list(owned.items())):
        try:
            if tab_id in browser.tab_ids:tab.close()
            if tab_id in browser.tab_ids:
                result['retained'].append(tab_id)
                result['errors'].append({'tab_id':tab_id,'error':'Tab remains open after close'})
            else:result['closed'].append(tab_id)
        except Exception as exc:
            result['retained'].append(tab_id)
            result['errors'].append({'tab_id':tab_id,'error':type(exc).__name__+': '+str(exc)[:250]})
    return result
