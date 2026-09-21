import { useEffect, useState } from 'react';
import axios from 'axios';
import { apiService } from '../services/api';
import type { MySQLSettings } from '../types';

interface Props { onClose: () => void }

export function MySQLSettingsModal({ onClose }: Props) {
  const [settings, setSettings] = useState<MySQLSettings | null>(null);
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('正在加载…');

  useEffect(() => {
    apiService.getMySQLSettings().then(value => {
      setSettings(value);
      setStatus('');
    }).catch(() => setStatus('无法加载配置。'));
  }, []);

  const save = async () => {
    if (!settings) return;
    setBusy(true);
    setStatus('正在保存…');
    try {
      await apiService.saveMySQLSettings({
        host: settings.host, port: settings.port, user: settings.user,
        databases: settings.databases, password,
      });
      setPassword('');
      setStatus('已保存并生效。现有会话会在下次提问时重新建立。');
    } catch (error) {
      const detail = axios.isAxiosError<{ detail?: string }>(error) ? error.response?.data?.detail : undefined;
      setStatus(detail || '保存失败，请检查配置内容或数据库连接。');
    } finally {
      setBusy(false);
    }
  };

  return <div className="business-layer-overlay" onClick={onClose}>
    <div className="business-layer-modal mysql-settings-modal" onClick={event => event.stopPropagation()}>
      <div className="business-layer-header">
        <div className="business-layer-title">MySQL 配置</div>
        <button className="icon-btn" onClick={onClose} aria-label="关闭">✕</button>
      </div>
      <div className="business-layer-hint">配置保存在后端 .env。数据库列表是允许查询的范围，用英文逗号分隔。保存时会验证连接并立即生效；请先等待正在运行的查询结束。</div>
      {settings && <div className="mysql-settings-fields">
        <label>主机<input value={settings.host} onChange={e => setSettings({ ...settings, host: e.target.value })} /></label>
        <label>端口<input type="number" min="1" max="65535" value={settings.port} onChange={e => setSettings({ ...settings, port: Number(e.target.value) })} /></label>
        <label>用户名<input value={settings.user} onChange={e => setSettings({ ...settings, user: e.target.value })} /></label>
        <label>密码<input type="password" autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} placeholder={settings.password_set ? '已设置；留空保持原密码' : '请输入密码'} /></label>
        <label>允许访问的数据库<input value={settings.databases} onChange={e => setSettings({ ...settings, databases: e.target.value })} placeholder="sales,reporting" /></label>
      </div>}
      <div className="business-layer-footer">
        <span className="business-layer-status" role="status">{status}</span>
        <button className="icon-btn" onClick={save} disabled={busy || !settings}>保存</button>
      </div>
    </div>
  </div>;
}
