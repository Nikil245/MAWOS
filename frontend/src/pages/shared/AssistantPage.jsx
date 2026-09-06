import { useEffect, useRef, useState } from 'react';
import { Bot, Send } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { api, isAbortError } from '../../services/api';
import { PageHeader } from '../../components/ui';

function responseSource(response) {
  if (response.source_label) return response.source_label;
  if (response.mode === 'llm') {
    return response.fallback ? 'Deterministic fallback after Ollama' : 'Ollama-grounded answer';
  }
  if (response.mode === 'scope') return 'Read-only assistant scope';
  const rejectedOllama = Boolean(
    response.fallback_code
    || response.fallback
    || response.routing?.deterministic_fallback,
  );
  return rejectedOllama ? 'Deterministic fallback' : 'Deterministic answer';
}

export default function AssistantPage() {
  const { token, user, checking } = useAuth();
  if (checking) return <AssistantLoading />;
  if (!token || !user) return null;
  const accountKey = `${token}:${user.id ?? user.username ?? user.usn ?? user.name}:${user.role}`;
  return <AssistantConversation key={accountKey} token={token} user={user} accountKey={accountKey} />;
}

const SAFE_GENERAL_PROMPTS = ['Explain machine learning simply.', 'What is SQL normalization?', 'What is MAWOS?'];
const ROLE_VALUES = new Set(['student', 'faculty', 'hod', 'principal', 'admin']);

function AssistantLoading() {
  return <main className="p-6"><p className="text-sm text-muted">Preparing your assistant…</p></main>;
}

function isCapabilityResponse(value, expectedRole) {
  return value && typeof value === 'object'
    && ROLE_VALUES.has(value.role) && value.role === expectedRole
    && ['title', 'subtitle', 'description', 'greeting', 'help', 'input_placeholder'].every(
      (key) => typeof value[key] === 'string')
    && Array.isArray(value.record_capabilities)
    && value.record_capabilities.every((item) => item && typeof item.category === 'string' && typeof item.scope === 'string')
    && Array.isArray(value.suggestion_groups)
    && value.suggestion_groups.every((group) => group && typeof group.label === 'string'
      && Array.isArray(group.prompts) && group.prompts.every((prompt) => typeof prompt === 'string'));
}

function errorKind(error) {
  if (['authentication', 'authorization', 'network', 'server', 'request'].includes(error?.category)) return error.category;
  return 'network';
}

function AssistantConversation({ token, user, accountKey }) {
  const contextTopic = useRef(null);
  const generalHistory = useRef([]);
  const pending = useRef(null);
  const capabilityRequest = useRef(null);
  const automaticRetryUsed = useRef(false);
  const submitting = useRef(false);
  const nextMessageId = useRef(0);
  const active = useRef(true);
  const [capability, setCapability] = useState(null);
  const [capabilityState, setCapabilityState] = useState({ status: 'loading', error: null });
  const [messages, setMessages] = useState([]);
  const [retryVersion, setRetryVersion] = useState(0);
  useEffect(() => {
    let cancelled = false;
    const requestId = Symbol(accountKey);
    active.current = true;
    contextTopic.current = null;
    generalHistory.current = [];
    pending.current?.abort();
    const controller = new AbortController();
    capabilityRequest.current = { requestId, controller };
    setCapability(null);
    setMessages([]);
    setCapabilityState({ status: 'loading', error: null });

    const load = async () => {
      try {
        const next = await api.assistantCapabilities(token, controller.signal);
        if (cancelled || capabilityRequest.current?.requestId !== requestId) return;
        if (!isCapabilityResponse(next, user.role)) {
          setCapabilityState({ status: 'error', error: 'malformed' });
          return;
        }
        setCapability(next);
        setMessages([{ id: `greeting-${accountKey}`, role: 'agent', text: next.greeting }]);
        setCapabilityState({ status: 'ready', error: null });
      } catch (error) {
        if (cancelled || isAbortError(error) || capabilityRequest.current?.requestId !== requestId) return;
        if (error?.category === 'authentication' && !automaticRetryUsed.current) {
          automaticRetryUsed.current = true;
          setRetryVersion((version) => version + 1);
          return;
        }
        setCapabilityState({ status: 'error', error: errorKind(error) });
      }
    };
    load();
    return () => {
      cancelled = true;
      active.current = false;
      contextTopic.current = null;
      generalHistory.current = [];
      controller.abort();
      pending.current?.abort();
    };
  }, [accountKey, retryVersion, token, user.role]);
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [loadingText, setLoadingText] = useState('Preparing your answer…');
  const append = (message) => setMessages((items) => [
    ...items.slice(-19), { ...message, id: message.id || `message-${++nextMessageId.current}` },
  ]);

  const send = async (event) => {
    event.preventDefault();
    const text = value.trim();
    if (!text || busy || submitting.current || (capabilityState.status === 'loading')) return;
    submitting.current = true;
    append({ role: 'user', text });
    setValue('');
    setBusy(true);
    setLoadingText(/\b(my|owe)\b.*(attendance|fees|marks)|show my marks/i.test(text)
      ? 'Looking up your authorized records…' : 'Preparing your answer…');
    pending.current = new AbortController();
    try {
      const response = await api.chat(
        token, text, pending.current.signal, contextTopic.current, generalHistory.current,
      );
      if (!active.current) return;
      if (!response || typeof response.text !== 'string' || !response.mode || !response.routing) {
        throw new Error('Invalid assistant response');
      }
      contextTopic.current = response.context_topic ?? null;
      if (response.mode === 'general_ai' && response.category === 'general_ai') {
        const next = [
          ...generalHistory.current,
          { role: 'user', content: text.slice(0, 700) },
          { role: 'assistant', content: response.text.slice(0, 700) },
        ].slice(-6);
        while (next.reduce((total, item) => total + item.content.length, 0) > 3000) next.shift();
        generalHistory.current = next;
      }
      append({
        role: 'agent', text: response.text, source: responseSource(response),
        note: response.mode === 'general_ai'
          ? 'Generated by the local AI model and may contain mistakes. Verify important information.'
          : null,
      });
    } catch {
      if (!active.current) return;
      contextTopic.current = null;
      append({
        role: 'error',
        text: 'The assistant could not complete that request. Your records were not changed. Please try again.',
      });
    } finally {
      submitting.current = false;
      if (active.current) setBusy(false);
    }
  };

  const retryCapabilities = () => {
    automaticRetryUsed.current = true;
    setRetryVersion((version) => version + 1);
  };
  const capabilityFailed = capabilityState.status === 'error';
  const canUseChat = capabilityState.status === 'ready' || capabilityFailed;

  return <>
    <PageHeader title={capability?.title || 'Academic assistant'} eyebrow="MAWOS / Role-scoped agent conversation">
      <p className="mt-1 text-sm text-muted">{capability?.description || 'Loading your authorized assistant scope…'}</p>
    </PageHeader>
    <section className="card mx-auto flex max-w-4xl flex-col overflow-hidden p-0" style={{ minHeight: '560px' }}>
      <div className="flex items-center gap-3 border-b p-4">
        <span className="rounded-lg bg-blue-50 p-2 text-primary"><Bot size={20} /></span>
        <div><p className="font-semibold">{capability?.title || 'Academic assistant'}</p><p className="text-xs text-muted">{capability?.subtitle || 'Loading authorized capabilities'}</p></div>
      </div>
      <div className="flex-1 space-y-4 overflow-auto p-5">
        {capabilityState.status === 'loading' && <div aria-live="polite" className="text-sm text-muted">Loading your authorized assistant scope…</div>}
        {capabilityFailed && <div className="rounded-xl bg-red-50 p-3 text-sm text-red-700">Assistant capabilities are temporarily unavailable. You can still ask general academic or MAWOS questions.<button type="button" className="ml-2 font-semibold underline" onClick={retryCapabilities}>Retry</button></div>}
        {messages.map((message) => <div className={`max-w-[85%] whitespace-pre-wrap rounded-xl p-3 text-sm leading-6 ${message.role === 'user' ? 'ml-auto bg-primary text-white' : message.role === 'error' ? 'bg-red-50 text-red-700' : 'bg-slate-100 text-slate-800'}`} key={message.id}>
          {message.source && <p className="mb-1 text-xs font-semibold opacity-70">{message.source}</p>}
          {message.text}
          {message.note && <p className="mt-2 text-xs text-amber-700">{message.note}</p>}
        </div>)}
        {busy && <div aria-live="polite" className="w-fit rounded-xl bg-slate-100 p-3 text-sm text-muted">{loadingText}</div>}
      </div>
      <div className="space-y-3 border-t px-4 py-3">
        {capability?.suggestion_groups.map((group) => <div key={group.label}>
          <p className="mb-1 text-xs font-semibold text-muted">{group.label}</p>
          <div className="flex flex-wrap gap-2">{group.prompts.map((prompt) => <button
            key={prompt} type="button" disabled={busy} onClick={() => setValue(prompt)}
            className="rounded-lg bg-slate-100 px-2 py-1 text-xs text-slate-700 hover:bg-blue-50"
          >{prompt}</button>)}</div>
        </div>)}
        {capabilityFailed && <div>
          <p className="mb-1 text-xs font-semibold text-muted">General help</p>
          <div className="flex flex-wrap gap-2">{SAFE_GENERAL_PROMPTS.map((prompt) => <button
            key={prompt} type="button" disabled={busy} onClick={() => setValue(prompt)}
            className="rounded-lg bg-slate-100 px-2 py-1 text-xs text-slate-700 hover:bg-blue-50"
          >{prompt}</button>)}</div>
        </div>}
      </div>
      <form className="flex gap-2 border-t p-4" onSubmit={send}>
        <label className="sr-only" htmlFor="chat">Ask a question</label>
        <input id="chat" className="field" disabled={!canUseChat} value={value} onChange={(event) => setValue(event.target.value)} placeholder={capability?.input_placeholder || (capabilityFailed ? 'Ask a general academic or MAWOS question…' : 'Loading authorized capabilities…')} />
        <button aria-label="Send message" className="btn-primary" disabled={busy || !canUseChat}><Send size={18} /></button>
      </form>
    </section>
  </>;
}
