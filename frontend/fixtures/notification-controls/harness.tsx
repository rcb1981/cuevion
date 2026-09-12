import React from 'react';
import { createRoot } from 'react-dom/client';
import { ServerNotifications } from '../../src/components/workspace/ServerNotifications';
const theme = new URLSearchParams(location.search).get('theme') || 'light';
document.documentElement.dataset.theme = theme;
const calls = [];
window.fixture = { calls };
const state = { orderedIds: [], byId: new Map(), firstPageLoaded: true, nextCursor: null, listLoading: false, loadMoreLoading: false, listError: null, loadMoreError: null };
const store = { ensureFirstPage: context => calls.push(['ensure',context]), refresh: () => calls.push(['refresh']), loadMore: () => calls.push(['more']) };
createRoot(document.getElementById('root')).render(<main className="min-h-screen bg-[var(--workspace-modal-bg)] p-8 text-[var(--workspace-text)]">
<h1 className="mb-6 text-xl">Notification secondary controls · {theme}</h1>
<div className="grid grid-cols-2 gap-6">{['ready','loading','retry'].flatMap(mode => [true,false].map(preview => <section key={`${mode}-${preview}`} data-case={`${mode}-${preview?'dashboard':'page'}`} className="rounded-2xl border border-[var(--workspace-border)] bg-[var(--workspace-card)] p-6">
<h2 className="mb-4 text-sm text-[var(--workspace-text-muted)]">{preview?'Dashboard preview':'Notifications page'} · {mode}</h2>
<ServerNotifications preview={preview} store={store} state={{...state,listLoading:mode==='loading',listError:mode==='retry'?'network_failure':null}} onOpen={()=>{}} />
</section>))}</div></main>);
