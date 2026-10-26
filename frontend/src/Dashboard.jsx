/**
 * WiFi Billing System - Admin Dashboard
 * Login page plus the admin / finance / viewer dashboard
 */

import React, { useCallback, useEffect, useState } from 'react';
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, PieChart, Pie, Cell
} from 'recharts';
import { API_BASE_URL, apiCall } from './api';
import { formatDuration } from './CustomerPortal';
import { GUIDES, NO_RADIUS_HELP, VENDORS, fillGuide, vendorLabel } from './routerGuides';
import './Dashboard.css';

// Plan colors follow the plan order, matching the customer portal
const COLORS = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899', '#14B8A6'];
const colorFor = (name, index) => COLORS[index % COLORS.length];

// Business name from the backend, for headings
const useBusinessName = () => {
  const [name, setName] = useState('');
  useEffect(() => {
    apiCall('/config').then((cfg) => setName(cfg.business_name)).catch(() => {});
  }, []);
  return name;
};

const kes = (value) => `KES ${Number(value || 0).toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
const dateTime = (value) => (value ? new Date(value).toLocaleString() : '-');

// Fetches an endpoint with the admin token; logs out on an expired session
const useApi = (token, onUnauthorized) =>
  useCallback(async (endpoint, options = {}) => {
    try {
      return await apiCall(endpoint, { ...options, token });
    } catch (error) {
      if (error.status === 401) onUnauthorized();
      throw error;
    }
  }, [token, onUnauthorized]);

// Login Component
export const LoginPage = ({ onLogin, onBack }) => {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const businessName = useBusinessName();

  const handleSubmit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError('');
    try {
      await onLogin(username, password);
    } catch (err) {
      setError(err.message || 'Invalid credentials');
      setLoading(false);
    }
  };

  return (
    <div className="login-container">
      <div className="login-card">
        <h1>{businessName || 'WiFi Billing'}</h1>
        <p className="subtitle">Admin Dashboard</p>

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Username</label>
            <input
              type="text"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Enter username"
              autoComplete="username"
              disabled={loading}
              autoFocus
            />
          </div>

          <div className="form-group">
            <label>Password</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Enter password"
              autoComplete="current-password"
              disabled={loading}
            />
          </div>

          {error && <div className="error-message">{error}</div>}

          <button type="submit" disabled={loading || !username || !password} className="btn btn-primary btn-block">
            {loading ? 'Logging in...' : 'Login'}
          </button>
        </form>
        {onBack && (
          <button className="btn-text" onClick={onBack}>← Back to customer portal</button>
        )}
      </div>
    </div>
  );
};

// Dashboard Component
const Dashboard = ({ token, admin, onLogout, onSwitchToCustomer }) => {
  const api = useApi(token, onLogout);
  const [dashboardData, setDashboardData] = useState(null);
  const [dailyRevenue, setDailyRevenue] = useState([]);
  const [voucherTypes, setVoucherTypes] = useState([]);
  const [days, setDays] = useState(30);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [activeTab, setActiveTab] = useState('overview');
  const businessName = useBusinessName();

  const fetchData = useCallback(async () => {
    setLoadError('');
    try {
      const [dashData, daily, types] = await Promise.all([
        api(`/analytics/dashboard?days=${days}`),
        api(`/analytics/revenue/daily?days=${days}`),
        api('/voucher-types?all=1')
      ]);
      setDashboardData(dashData);
      setDailyRevenue(daily);
      setVoucherTypes(types);
    } catch (error) {
      console.error('Error fetching data:', error);
      setLoadError(error.message);
    }
    setLoading(false);
  }, [api, days]);

  useEffect(() => {
    fetchData();
  }, [fetchData]);

  if (loading) {
    return <div className="loading">Loading dashboard...</div>;
  }

  const typeNames = voucherTypes.map((t) => t.name);
  const chartData = dailyRevenue.map((item) => ({
    ...item,
    label: new Date(`${item.date}T00:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
  }));

  const tabs = [
    ['overview', 'Overview'],
    ['revenue', 'Revenue Analytics'],
    ['transactions', 'Transactions'],
    ['vouchers', 'Vouchers'],
    ['customers', 'Customers'],
    ...(admin.role === 'admin' ? [['plans', 'Plans'], ['router', 'Routers']] : []),
    ['account', 'Account']
  ];

  const periodFilter = (
    <div className="filter-bar">
      <label>Period:</label>
      <select value={days} onChange={(e) => setDays(parseInt(e.target.value, 10))}>
        <option value={7}>Last 7 days</option>
        <option value={30}>Last 30 days</option>
        <option value={90}>Last 90 days</option>
      </select>
      <button className="btn btn-secondary btn-small" onClick={fetchData}>Refresh</button>
    </div>
  );

  return (
    <div className="dashboard-container">
      {/* Header */}
      <div className="dashboard-header">
        <div>
          <h1>{businessName || 'WiFi Billing'}</h1>
          <p className="user-info">Welcome, {admin.username} ({admin.role})</p>
        </div>
        <div className="header-actions">
          {onSwitchToCustomer && (
            <button className="btn btn-secondary" onClick={onSwitchToCustomer}>Customer Portal</button>
          )}
          <button className="btn btn-secondary" onClick={onLogout}>Logout</button>
        </div>
      </div>

      {/* Navigation Tabs */}
      <div className="tabs">
        {tabs.map(([key, label]) => (
          <button
            key={key}
            className={`tab ${activeTab === key ? 'active' : ''}`}
            onClick={() => setActiveTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {loadError && <div className="error-message banner">Could not load data: {loadError}</div>}
      {!loadError && voucherTypes.length === 0 && (
        <div className="notice-banner">
          Welcome! Your system is empty. {admin.role === 'admin'
            ? <>Start by adding your WiFi plans and prices in the <button className="link-button" onClick={() => setActiveTab('plans')}>Plans</button> tab, then connect your hotspot in the <button className="link-button" onClick={() => setActiveTab('router')}>Router</button> tab.</>
            : 'An admin needs to add plans before customers can buy vouchers.'}
        </div>
      )}

      {/* Overview Tab */}
      {activeTab === 'overview' && (
        <div className="dashboard-content">
          {periodFilter}
          <div className="kpi-grid">
            <div className="kpi-card">
              <div className="kpi-label">Total Revenue</div>
              <div className="kpi-value">{kes(dashboardData?.total_revenue)}</div>
              <div className="kpi-period">Last {days} days</div>
            </div>

            <div className="kpi-card">
              <div className="kpi-label">Transactions</div>
              <div className="kpi-value">{(dashboardData?.total_transactions || 0).toLocaleString()}</div>
              <div className="kpi-period">
                Completed · {dashboardData?.failed_transactions || 0} failed · {dashboardData?.pending_transactions || 0} pending
              </div>
            </div>

            <div className="kpi-card">
              <div className="kpi-label">Customers</div>
              <div className="kpi-value">{(dashboardData?.total_customers || 0).toLocaleString()}</div>
              <div className="kpi-period">{dashboardData?.new_customers || 0} new in period</div>
            </div>

            <div className="kpi-card">
              <div className="kpi-label">Active Vouchers</div>
              <div className="kpi-value">{(dashboardData?.active_vouchers || 0).toLocaleString()}</div>
              <div className="kpi-period">Online right now</div>
            </div>

            <div className="kpi-card">
              <div className="kpi-label">Avg Transaction</div>
              <div className="kpi-value">
                {kes(dashboardData?.total_transactions > 0
                  ? dashboardData.total_revenue / dashboardData.total_transactions
                  : 0)}
              </div>
              <div className="kpi-period">Value</div>
            </div>
          </div>

          {/* Revenue by Type Pie Chart */}
          <div className="chart-container">
            <h2>Revenue by Voucher Type</h2>
            {dashboardData?.revenue_by_type?.length ? (
              <ResponsiveContainer width="100%" height={300}>
                <PieChart>
                  <Pie
                    data={dashboardData.revenue_by_type}
                    dataKey="revenue"
                    nameKey="name"
                    cx="50%"
                    cy="50%"
                    outerRadius={100}
                    label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                  >
                    {dashboardData.revenue_by_type.map((entry, index) => (
                      <Cell key={`cell-${index}`} fill={colorFor(entry.name, index)} />
                    ))}
                  </Pie>
                  <Tooltip formatter={(value) => kes(value)} />
                  <Legend />
                </PieChart>
              </ResponsiveContainer>
            ) : (
              <p className="empty-state">No sales in this period yet.</p>
            )}
          </div>
        </div>
      )}

      {/* Revenue Analytics Tab */}
      {activeTab === 'revenue' && (
        <div className="dashboard-content">
          {periodFilter}

          {/* Daily Revenue Chart */}
          <div className="chart-container">
            <h2>Daily Revenue by Voucher Type</h2>
            <ResponsiveContainer width="100%" height={400}>
              <LineChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <XAxis dataKey="label" stroke="#94A3B8" />
                <YAxis stroke="#94A3B8" />
                <Tooltip
                  formatter={(value) => kes(value)}
                  contentStyle={{ backgroundColor: '#1F2937', border: 'none', borderRadius: '8px', color: '#FFF' }}
                />
                <Legend />
                {typeNames.map((type, index) => (
                  <Line
                    key={type}
                    type="monotone"
                    dataKey={type}
                    stroke={colorFor(type, index)}
                    strokeWidth={2}
                    dot={false}
                  />
                ))}
              </LineChart>
            </ResponsiveContainer>
          </div>

          {/* Detailed Revenue Table */}
          <div className="table-container">
            <h2>Daily Revenue Details</h2>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Date</th>
                    {typeNames.map((type) => <th key={type}>{type}</th>)}
                    <th>Sales</th>
                    <th>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {[...chartData].reverse().map((row) => (
                    <tr key={row.date}>
                      <td>{row.label}</td>
                      {typeNames.map((type) => <td key={type}>{kes(row[type])}</td>)}
                      <td>{row.count}</td>
                      <td className="total">{kes(row.total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {activeTab === 'transactions' && (
        <div className="dashboard-content">
          <TransactionList api={api} canResend={admin.role !== 'viewer'} />
        </div>
      )}

      {activeTab === 'vouchers' && (
        <div className="dashboard-content">
          <VoucherList api={api} voucherTypes={voucherTypes} canGenerate={admin.role === 'admin'} />
        </div>
      )}

      {/* Customers Tab */}
      {activeTab === 'customers' && (
        <div className="dashboard-content">
          <CustomerList api={api} canEdit={admin.role === 'admin'} />
        </div>
      )}

      {activeTab === 'account' && (
        <div className="dashboard-content">
          <ChangePassword api={api} admin={admin} />
          {admin.role === 'admin' && <UserManager api={api} currentId={admin.id} />}
        </div>
      )}

      {activeTab === 'router' && (
        <div className="dashboard-content">
          <RoutersTab api={api} />
        </div>
      )}

      {activeTab === 'plans' && (
        <div className="dashboard-content">
          <PlanManager api={api} voucherTypes={voucherTypes} onChange={fetchData} />
        </div>
      )}
    </div>
  );
};

const StatusBadge = ({ status }) => <span className={`badge ${status}`}>{status}</span>;

// Transaction List Component
const TransactionList = ({ api, canResend }) => {
  const [transactions, setTransactions] = useState([]);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(true);
  const [notice, setNotice] = useState(null);

  const resendSms = async (tx) => {
    setNotice(null);
    try {
      const res = await api(`/transactions/${tx.id}/resend-sms`, { method: 'POST' });
      setNotice({ ok: true, text: res.message });
    } catch (error) {
      setNotice({ ok: false, text: error.message });
    }
  };

  useEffect(() => {
    setLoading(true);
    api(`/transactions?limit=300${status ? `&status=${status}` : ''}`)
      .then(setTransactions)
      .catch((error) => console.error('Error fetching transactions:', error))
      .finally(() => setLoading(false));
  }, [api, status]);

  return (
    <div className="table-container">
      <div className="table-header">
        <h2>Transactions</h2>
        <div className="filter-bar compact">
          <label>Status:</label>
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All</option>
            <option value="completed">Completed</option>
            <option value="pending">Pending</option>
            <option value="failed">Failed</option>
          </select>
        </div>
      </div>
      {notice && <div className={notice.ok ? 'success-message-box' : 'error-message'}>{notice.text}</div>}
      {loading ? <div className="empty-state">Loading transactions...</div> : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Date</th>
                <th>Phone</th>
                <th>Plan</th>
                <th>Amount</th>
                <th>Status</th>
                <th>M-Pesa Receipt</th>
                <th>Voucher</th>
                <th>Note</th>
                {canResend && <th></th>}
              </tr>
            </thead>
            <tbody>
              {transactions.map((tx) => (
                <tr key={tx.id}>
                  <td>{dateTime(tx.created_at)}</td>
                  <td>{tx.phone}</td>
                  <td>{tx.voucher_type}</td>
                  <td>{kes(tx.amount)}</td>
                  <td><StatusBadge status={tx.status} /></td>
                  <td className="mono">{tx.mpesa_receipt || '-'}</td>
                  <td className="mono">{tx.voucher_code || '-'}</td>
                  <td className="note">{tx.result_desc || '-'}</td>
                  {canResend && (
                    <td>
                      {tx.voucher_code && (
                        <button className="btn-text small" onClick={() => resendSms(tx)}>Resend SMS</button>
                      )}
                    </td>
                  )}
                </tr>
              ))}
              {transactions.length === 0 && (
                <tr><td colSpan={canResend ? 9 : 8} className="empty-state">No transactions found.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

// Voucher List Component
const VoucherList = ({ api, voucherTypes, canGenerate }) => {
  const [vouchers, setVouchers] = useState([]);
  const [status, setStatus] = useState('');
  const [loading, setLoading] = useState(true);
  const [typeId, setTypeId] = useState('');
  const [count, setCount] = useState(10);
  const [generated, setGenerated] = useState([]);
  const [message, setMessage] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    api(`/vouchers?limit=300${status ? `&status=${status}` : ''}`)
      .then(setVouchers)
      .catch((error) => console.error('Error fetching vouchers:', error))
      .finally(() => setLoading(false));
  }, [api, status]);

  useEffect(load, [load]);

  const generate = async (e) => {
    e.preventDefault();
    setMessage('');
    try {
      const created = await api('/vouchers/generate', {
        method: 'POST',
        body: JSON.stringify({ voucher_type_id: Number(typeId || voucherTypes[0]?.id), count: Number(count) })
      });
      setGenerated(created);
      load();
    } catch (error) {
      setMessage(error.message);
    }
  };

  return (
    <>
      {canGenerate && (
        <div className="table-container">
          <h2>Generate Vouchers</h2>
          <p className="hint">Create unused codes to sell for cash or print. Each one starts running on first login.</p>
          <form className="inline-form" onSubmit={generate}>
            <select value={typeId} onChange={(e) => setTypeId(e.target.value)}>
              {voucherTypes.map((t) => <option key={t.id} value={t.id}>{t.name} ({kes(t.price_ksh)})</option>)}
            </select>
            <input type="number" min="1" max="500" value={count} onChange={(e) => setCount(e.target.value)} />
            <button type="submit" className="btn btn-primary">Generate</button>
          </form>
          {message && <div className="error-message">{message}</div>}
          {generated.length > 0 && (
            <div className="code-list">
              {generated.map((v) => <span key={v.id} className="code-chip">{v.code}</span>)}
            </div>
          )}
        </div>
      )}

      <div className="table-container">
        <div className="table-header">
          <h2>Vouchers</h2>
          <div className="filter-bar compact">
            <label>Status:</label>
            <select value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">All</option>
              <option value="active">Active</option>
              <option value="unused">Unused</option>
              <option value="expired">Expired</option>
            </select>
          </div>
        </div>
        {loading ? <div className="empty-state">Loading vouchers...</div> : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Code</th>
                  <th>Plan</th>
                  <th>Phone</th>
                  <th>Status</th>
                  <th>Created</th>
                  <th>Expires</th>
                </tr>
              </thead>
              <tbody>
                {vouchers.map((v) => (
                  <tr key={v.id}>
                    <td className="mono">{v.code}</td>
                    <td>{v.voucher_type}</td>
                    <td>{v.phone || '-'}</td>
                    <td><StatusBadge status={v.status} /></td>
                    <td>{dateTime(v.created_at)}</td>
                    <td>{dateTime(v.expires_at)}</td>
                  </tr>
                ))}
                {vouchers.length === 0 && (
                  <tr><td colSpan={6} className="empty-state">No vouchers found.</td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
};

// Customer List Component
const CustomerList = ({ api, canEdit }) => {
  const [customers, setCustomers] = useState([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    api(`/customers${search ? `?search=${encodeURIComponent(search)}` : ''}`)
      .then(setCustomers)
      .catch((error) => console.error('Error fetching customers:', error))
      .finally(() => setLoading(false));
  }, [api, search]);

  useEffect(() => {
    const timer = setTimeout(load, 250);
    return () => clearTimeout(timer);
  }, [load]);

  const toggleActive = async (customer) => {
    try {
      await api(`/customers/${customer.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ is_active: !customer.is_active })
      });
      load();
    } catch (error) {
      alert(error.message);
    }
  };

  return (
    <div className="table-container">
      <div className="table-header">
        <h2>Customer List</h2>
        <input
          className="search-input"
          placeholder="Search phone or name"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>
      {loading ? <div className="empty-state">Loading customers...</div> : (
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr>
                <th>Phone</th>
                <th>Name</th>
                <th>Email</th>
                <th>Total Spent</th>
                <th>Status</th>
                <th>Joined</th>
                {canEdit && <th></th>}
              </tr>
            </thead>
            <tbody>
              {customers.map((customer) => (
                <tr key={customer.id}>
                  <td>{customer.phone}</td>
                  <td>{customer.name || '-'}</td>
                  <td>{customer.email || '-'}</td>
                  <td>{kes(customer.total_spent)}</td>
                  <td>
                    <span className={`badge ${customer.is_active ? 'active' : 'inactive'}`}>
                      {customer.is_active ? 'Active' : 'Suspended'}
                    </span>
                  </td>
                  <td>{new Date(customer.created_at).toLocaleDateString()}</td>
                  {canEdit && (
                    <td>
                      <button className="btn-text small" onClick={() => toggleActive(customer)}>
                        {customer.is_active ? 'Suspend' : 'Reactivate'}
                      </button>
                    </td>
                  )}
                </tr>
              ))}
              {customers.length === 0 && (
                <tr><td colSpan={canEdit ? 7 : 6} className="empty-state">No customers found.</td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
};

// Change Password Component
const ChangePassword = ({ api, admin }) => {
  const [form, setForm] = useState({ current: '', next: '', confirm: '' });
  const [notice, setNotice] = useState(null);
  const [saving, setSaving] = useState(false);
  const set = (field) => (e) => setForm({ ...form, [field]: e.target.value });

  const submit = async (e) => {
    e.preventDefault();
    setNotice(null);
    if (form.next !== form.confirm) {
      setNotice({ ok: false, text: 'New passwords do not match' });
      return;
    }
    setSaving(true);
    try {
      await api('/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({ current_password: form.current, new_password: form.next })
      });
      setForm({ current: '', next: '', confirm: '' });
      setNotice({ ok: true, text: 'Password changed' });
    } catch (error) {
      setNotice({ ok: false, text: error.message });
    }
    setSaving(false);
  };

  return (
    <div className="table-container narrow-card">
      <h2>Account</h2>
      <p className="hint">Signed in as <strong>{admin.username}</strong> ({admin.role}).</p>
      <form onSubmit={submit}>
        <div className="form-group">
          <label>Current password</label>
          <input type="password" autoComplete="current-password" value={form.current} onChange={set('current')} required />
        </div>
        <div className="form-group">
          <label>New password (at least 8 characters)</label>
          <input type="password" autoComplete="new-password" minLength={8} value={form.next} onChange={set('next')} required />
        </div>
        <div className="form-group">
          <label>Confirm new password</label>
          <input type="password" autoComplete="new-password" value={form.confirm} onChange={set('confirm')} required />
        </div>
        {notice && <div className={notice.ok ? 'success-message-box' : 'error-message'}>{notice.text}</div>}
        <button type="submit" className="btn btn-primary btn-small" disabled={saving}>
          {saving ? 'Saving...' : 'Change Password'}
        </button>
      </form>
    </div>
  );
};

// Dashboard users, admin only
const UserManager = ({ api, currentId }) => {
  const [users, setUsers] = useState([]);
  const [form, setForm] = useState({ username: '', password: '', role: 'finance' });
  const [notice, setNotice] = useState(null);

  const load = useCallback(() => {
    api('/admins').then(setUsers).catch((error) => setNotice({ ok: false, text: error.message }));
  }, [api]);
  useEffect(load, [load]);

  const create = async (e) => {
    e.preventDefault();
    setNotice(null);
    try {
      await api('/admins', { method: 'POST', body: JSON.stringify(form) });
      setForm({ username: '', password: '', role: 'finance' });
      setNotice({ ok: true, text: 'User added' });
      load();
    } catch (error) {
      setNotice({ ok: false, text: error.message });
    }
  };

  const update = async (user, changes) => {
    setNotice(null);
    try {
      await api(`/admins/${user.id}`, { method: 'PATCH', body: JSON.stringify(changes) });
      load();
    } catch (error) {
      setNotice({ ok: false, text: error.message });
    }
  };

  const resetPassword = (user) => {
    const password = window.prompt(`New password for ${user.username} (8+ characters):`);
    if (password) update(user, { password });
  };

  return (
    <div className="table-container">
      <h2>Dashboard Users</h2>
      <p className="hint">Admins can do everything. Finance and viewer users can see reports; finance can also resend SMS.</p>
      {notice && <div className={notice.ok ? 'success-message-box' : 'error-message'}>{notice.text}</div>}
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr><th>Username</th><th>Role</th><th>Status</th><th>Last login</th><th></th></tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.id}>
                <td>{user.username}{user.id === currentId && ' (you)'}</td>
                <td>
                  <select className="cell-input narrow" value={user.role} disabled={user.id === currentId}
                    onChange={(e) => update(user, { role: e.target.value })}>
                    <option value="admin">admin</option>
                    <option value="finance">finance</option>
                    <option value="viewer">viewer</option>
                  </select>
                </td>
                <td><span className={`badge ${user.is_active ? 'active' : 'inactive'}`}>{user.is_active ? 'Active' : 'Disabled'}</span></td>
                <td>{dateTime(user.last_login)}</td>
                <td className="row-actions">
                  <button className="btn-text small" onClick={() => resetPassword(user)}>Reset password</button>
                  {user.id !== currentId && (
                    <button className="btn-text small" onClick={() => update(user, { is_active: !user.is_active })}>
                      {user.is_active ? 'Disable' : 'Enable'}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h2 className="spaced">Add User</h2>
      <form className="inline-form" onSubmit={create}>
        <input placeholder="Username" value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required />
        <input type="password" placeholder="Password (8+)" minLength={8} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} required />
        <select value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
          <option value="finance">finance</option>
          <option value="viewer">viewer</option>
          <option value="admin">admin</option>
        </select>
        <button type="submit" className="btn btn-primary">Add</button>
      </form>
    </div>
  );
};

// Routers that log customers in through the built-in RADIUS server, admin only
const RoutersTab = ({ api }) => {
  const [radius, setRadius] = useState(null);
  const [routers, setRouters] = useState([]);
  const [online, setOnline] = useState([]);
  const [form, setForm] = useState({ name: '', vendor: 'mikrotik', address: '', secret: '' });
  const [guideFor, setGuideFor] = useState(null);
  const [showSecret, setShowSecret] = useState({});
  const [notice, setNotice] = useState(null);

  const load = useCallback(() => {
    Promise.all([api('/radius/status'), api('/routers'), api('/sessions?online=1')])
      .then(([status, list, sessions]) => {
        setRadius(status);
        setRouters(list);
        setOnline(sessions);
      })
      .catch((error) => setNotice({ ok: false, text: error.message }));
  }, [api]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 15000);
    return () => clearInterval(timer);
  }, [load]);

  const addRouter = async (e) => {
    e.preventDefault();
    setNotice(null);
    try {
      const created = await api('/routers', { method: 'POST', body: JSON.stringify(form) });
      setForm({ name: '', vendor: form.vendor, address: '', secret: '' });
      setGuideFor(created.id);
      load();
    } catch (error) {
      setNotice({ ok: false, text: error.message });
    }
  };

  const changeRouter = async (router, changes) => {
    setNotice(null);
    try {
      await api(`/routers/${router.id}`, { method: 'PATCH', body: JSON.stringify(changes) });
      load();
    } catch (error) {
      setNotice({ ok: false, text: error.message });
    }
  };

  const removeRouter = async (router) => {
    if (!window.confirm(`Remove ${router.name}? Its customers will no longer be able to log in.`)) return;
    try {
      await api(`/routers/${router.id}`, { method: 'DELETE' });
      if (guideFor === router.id) setGuideFor(null);
      load();
    } catch (error) {
      setNotice({ ok: false, text: error.message });
    }
  };

  if (!radius) return notice ? <div className="error-message">{notice.text}</div> : <div className="empty-state">Loading...</div>;

  const guideRouter = routers.find((r) => r.id === guideFor);
  const hosts = radius.walled_garden_hosts;
  const values = guideRouter && {
    server: radius.server_host || '<server IP>',
    auth: radius.auth_port,
    acct: radius.acct_port,
    secret: guideRouter.secret,
    portal: radius.portal_url || '<your portal address>',
    hosts: hosts.length ? hosts.join(', ') : '<your portal host>',
    mikrotikWalledGarden: hosts.map((h) => (/^[\d.]+$/.test(h)
      ? `/ip hotspot walled-garden ip add dst-address=${h} action=accept`
      : `/ip hotspot walled-garden add dst-host=${h}\n/ip hotspot walled-garden ip add dst-host=${h} action=accept`)).join('\n')
  };
  const seenRecently = (r) => r.last_seen_at && Date.now() - new Date(r.last_seen_at).getTime() < 15 * 60 * 1000;

  return (
    <>
      <div className="table-container">
        <h2>Connect your routers</h2>
        <p className="hint">
          Works with any router, access point controller or firewall that has a hotspot / captive portal with RADIUS:
          MikroTik, UniFi, TP-Link Omada, pfSense, OPNsense, OpenWrt, FortiGate, Cisco, Ruckus and more. Customers log in
          with their voucher code; this system checks it, applies the plan's time and speed, and keeps each code on one device.
        </p>
        {notice && <div className={notice.ok ? 'success-message-box' : 'error-message'}>{notice.text}</div>}
        <div className="kpi-grid">
          <div className="kpi-card">
            <div className="kpi-label">RADIUS server</div>
            <div className={`kpi-value small ${radius.running ? 'ok' : 'warn'}`}>
              {!radius.enabled ? 'Off' : radius.running ? 'Running' : 'Not running'}
            </div>
            <div className="kpi-period">{radius.server_host || '?'} · UDP {radius.auth_port} / {radius.acct_port}</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-label">Routers</div>
            <div className="kpi-value small">{routers.length}</div>
            <div className="kpi-period">{routers.filter(seenRecently).length} heard from in the last 15 min</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-label">Online now</div>
            <div className="kpi-value small">{online.length}</div>
            <div className="kpi-period">Customers connected</div>
          </div>
        </div>
        {radius.enabled && !radius.running && (
          <div className="error-message">
            The RADIUS server isn't running. Start the backend with <code>python billing_system_backend.py</code> (it starts
            RADIUS too) or run <code>python radius_server.py</code>. Cloud hosts without UDP (like Render) can't run it:
            use a VPS or a PC on your network, or the MikroTik-only sync below.
          </div>
        )}
        {radius.server_host_is_local && (
          <div className="error-message">
            Routers can't reach "{radius.server_host || 'localhost'}". Set RADIUS_PUBLIC_HOST (or PUBLIC_API_URL) to this
            server's LAN IP or public address so the setup steps show the right value.
          </div>
        )}
        {radius.unknown_router_ip && (
          <div className="notice-banner inline">
            A device at <strong>{radius.unknown_router_ip}</strong> is sending RADIUS requests but isn't added below, so it's
            being ignored.{' '}
            <button className="link-button" onClick={() => setForm({ ...form, address: radius.unknown_router_ip })}>Add it</button>
          </div>
        )}
      </div>

      <div className="table-container">
        <h2>Routers</h2>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr><th>Name</th><th>Type</th><th>Address</th><th>Shared secret</th><th>Last seen</th><th>Online</th><th></th></tr>
            </thead>
            <tbody>
              {routers.map((r) => (
                <tr key={r.id}>
                  <td>{r.name}</td>
                  <td>{vendorLabel(r.vendor)}</td>
                  <td className="mono">{r.address}</td>
                  <td className="mono">
                    {showSecret[r.id] ? r.secret : '••••••••'}{' '}
                    <button className="btn-text small" onClick={() => setShowSecret({ ...showSecret, [r.id]: !showSecret[r.id] })}>
                      {showSecret[r.id] ? 'Hide' : 'Show'}
                    </button>
                  </td>
                  <td>
                    <span className={`badge ${seenRecently(r) ? 'active' : 'inactive'}`}>
                      {r.last_seen_at ? dateTime(r.last_seen_at) : 'Never'}
                    </span>
                  </td>
                  <td>{r.online_sessions}</td>
                  <td className="row-actions">
                    <button className="btn-text small" onClick={() => setGuideFor(guideFor === r.id ? null : r.id)}>Setup steps</button>
                    <button className="btn-text small" onClick={() => changeRouter(r, { is_active: !r.is_active })}>
                      {r.is_active ? 'Disable' : 'Enable'}
                    </button>
                    <button className="btn-text small danger" onClick={() => removeRouter(r)}>Remove</button>
                  </td>
                </tr>
              ))}
              {routers.length === 0 && (
                <tr><td colSpan={7} className="empty-state">No routers yet. Add your first one below.</td></tr>
              )}
            </tbody>
          </table>
        </div>

        <h2 className="spaced">Add Router</h2>
        <form className="inline-form" onSubmit={addRouter}>
          <input placeholder="Name, e.g. Main office" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
          <select value={form.vendor} onChange={(e) => setForm({ ...form, vendor: e.target.value })}>
            {VENDORS.map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
          <input placeholder="Router IP or range" value={form.address} onChange={(e) => setForm({ ...form, address: e.target.value })} required />
          <input placeholder="Secret (blank = generate)" value={form.secret} onChange={(e) => setForm({ ...form, secret: e.target.value })} />
          <button type="submit" className="btn btn-primary">Add</button>
        </form>
        <p className="hint spaced-top">
          The address is where the router's RADIUS requests come from: its LAN IP if it's on the same network as this server,
          or its public IP (or ISP range) if this server is in the cloud. For UniFi/Omada use the controller or gateway that sends them.
        </p>
        <p className="hint"><strong>Tenda, TP-Link home routers and similar:</strong> {NO_RADIUS_HELP}</p>
      </div>

      {guideRouter && (
        <div className="table-container">
          <h2>Setup steps: {guideRouter.name} ({vendorLabel(guideRouter.vendor)})</h2>
          <ol className="guide-steps">
            {GUIDES[guideRouter.vendor].steps.map((step, i) => {
              const text = fillGuide(step, values);
              return (
                <li key={i}>
                  {text.includes('\n') ? (
                    <>
                      {text.split('\n')[0]}
                      <pre className="code-block">{text.split('\n').slice(1).join('\n')}</pre>
                    </>
                  ) : text}
                </li>
              );
            })}
          </ol>
        </div>
      )}

      <div className="table-container">
        <h2>Online now</h2>
        <div className="table-scroll">
          <table className="data-table">
            <thead>
              <tr><th>Code</th><th>Plan</th><th>Router</th><th>Device (MAC)</th><th>IP</th><th>Since</th><th>Down / Up</th></tr>
            </thead>
            <tbody>
              {online.map((s) => (
                <tr key={s.id}>
                  <td className="mono">{s.code}</td>
                  <td>{s.voucher_type || '-'}</td>
                  <td>{s.router || '-'}</td>
                  <td className="mono">{s.mac || '-'}</td>
                  <td className="mono">{s.ip || '-'}</td>
                  <td>{dateTime(s.started_at)}</td>
                  <td>{s.download_mb} MB / {s.upload_mb} MB</td>
                </tr>
              ))}
              {online.length === 0 && <tr><td colSpan={7} className="empty-state">Nobody is connected right now.</td></tr>}
            </tbody>
          </table>
        </div>
      </div>

      {radius.router_mode !== 'off' ? <MikrotikSync api={api} /> : (
        <div className="table-container">
          <h2>MikroTik login page</h2>
          <p className="hint">
            Optional branded hotspot login page with a "Buy with M-Pesa" button. Upload it to the router as hotspot/login.html.
          </p>
          <DownloadLoginPage onError={(text) => setNotice({ ok: false, text })} />
        </div>
      )}
    </>
  );
};

const DownloadLoginPage = ({ onError }) => {
  const download = async () => {
    try {
      const response = await fetch(`${API_BASE_URL}/router/login.html`, {
        headers: { Authorization: `Bearer ${localStorage.getItem('auth_token')}` }
      });
      if (!response.ok) throw new Error(`Download failed (${response.status})`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = 'login.html';
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      onError(error.message);
    }
  };
  return <button className="btn btn-primary btn-small" onClick={download}>Download login.html</button>;
};

// MikroTik-only direct sync (ROUTER_MODE=pull/api), for servers RADIUS can't reach
const MikrotikSync = ({ api }) => {
  const [status, setStatus] = useState(null);
  const [notice, setNotice] = useState(null);
  const [copied, setCopied] = useState(false);

  const load = useCallback(() => {
    api('/router/status').then(setStatus).catch((error) => setNotice({ ok: false, text: error.message }));
  }, [api]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 15000);
    return () => clearInterval(timer);
  }, [load]);

  const syncNow = async () => {
    setNotice(null);
    try {
      const res = await api('/router/sync', { method: 'POST' });
      setNotice({ ok: true, text: `Synced: ${res.added} added, ${res.removed} removed` });
      load();
    } catch (error) {
      setNotice({ ok: false, text: error.message });
    }
  };

  const downloadLoginPage = async () => {
    setNotice(null);
    try {
      const response = await fetch(`${API_BASE_URL}/router/login.html`, {
        headers: { Authorization: `Bearer ${localStorage.getItem('auth_token')}` }
      });
      if (!response.ok) throw new Error(`Download failed (${response.status})`);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = 'login.html';
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setNotice({ ok: false, text: error.message });
    }
  };

  const copyScript = async () => {
    try {
      await navigator.clipboard.writeText(status.setup_script);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  };

  if (!status) return notice ? <div className="error-message">{notice.text}</div> : <div className="empty-state">Loading...</div>;

  const syncedRecently = status.last_sync_at &&
    Date.now() - new Date(status.last_sync_at).getTime() < Math.max(status.sync_interval * 3, 60) * 1000;

  return (
    <>
      <div className="table-container">
        <h2>MikroTik direct sync</h2>
        <p className="hint">
          Each voucher becomes a login on your MikroTik hotspot (username and password are the voucher code),
          with the plan's speed limit. Expired vouchers are removed and disconnected automatically.
          Users you created on the router yourself are left alone.
        </p>
        {notice && <div className={notice.ok ? 'success-message-box' : 'error-message'}>{notice.text}</div>}
        <div className="kpi-grid">
          <div className="kpi-card">
            <div className="kpi-label">Mode</div>
            <div className="kpi-value small">{status.mode}</div>
            <div className="kpi-period">
              {status.mode === 'pull' && `Router checks in every ${status.sync_interval}s`}
              {status.mode === 'api' && `Pushing to ${status.mikrotik_host || '(MIKROTIK_HOST not set)'}`}
              {status.mode === 'off' && 'Router integration is off'}
            </div>
          </div>
          <div className="kpi-card">
            <div className="kpi-label">Last sync</div>
            <div className={`kpi-value small ${syncedRecently ? 'ok' : 'warn'}`}>
              {status.last_sync_at ? (syncedRecently ? 'Connected' : 'Not recently') : 'Never'}
            </div>
            <div className="kpi-period">{status.last_sync_at ? `${dateTime(status.last_sync_at)} · ${status.last_sync}` : 'Waiting for the router'}</div>
          </div>
          <div className="kpi-card">
            <div className="kpi-label">Codes on router</div>
            <div className="kpi-value small">{status.voucher_count}</div>
            <div className="kpi-period">Unused + active vouchers</div>
          </div>
        </div>
        {status.last_error && <div className="error-message">Last error: {status.last_error}</div>}
        {status.mode === 'api' && <button className="btn btn-primary btn-small" onClick={syncNow}>Sync now</button>}
      </div>

      {status.mode === 'pull' && (
        <div className="table-container">
          <h2>Step 1: Run this once on your router</h2>
          <p className="hint">
            Open Winbox, click <strong>New Terminal</strong>, paste the script and press Enter. It needs RouterOS v7
            and a working hotspot (IP &gt; Hotspot &gt; Hotspot Setup). The script contains a secret key, so don't share it.
          </p>
          {status.api_url_is_local && (
            <div className="error-message">
              The script points the router at {status.api_url}, which the router can't reach. Set PUBLIC_API_URL in the
              backend settings to an address the router can reach: your public domain, or this computer's network
              address such as http://192.168.88.10:5000/api if the system runs on a PC in the same network.
            </div>
          )}
          {!status.api_url_is_local && status.walled_garden_hosts.length === 0 && (
            <div className="error-message">
              PORTAL_URL and PUBLIC_API_URL are not set, so the script can't add your payment page to the walled garden
              and may point the router at an address it can't reach. Set them in the backend settings first.
            </div>
          )}
          <pre className="code-block">{status.setup_script}</pre>
          <button className="btn btn-primary btn-small" onClick={copyScript}>{copied ? 'Copied!' : 'Copy script'}</button>
        </div>
      )}

      {status.mode !== 'off' && (
        <div className="table-container">
          <h2>{status.mode === 'pull' ? 'Step 2' : 'Step 1'}: Install the login page</h2>
          <p className="hint">
            Customers see this page when they join your WiFi. It takes their voucher code and has a button to buy one with
            M-Pesa. In Winbox open <strong>Files</strong>, open the <code>hotspot</code> folder and replace <code>login.html</code> with
            this file (keep a copy of the old one).
          </p>
          <button className="btn btn-primary btn-small" onClick={downloadLoginPage}>Download login.html</button>
        </div>
      )}
    </>
  );
};

// Plan (voucher type) management, admin only
const PlanManager = ({ api, voucherTypes, onChange }) => {
  const [drafts, setDrafts] = useState({});
  const [newPlan, setNewPlan] = useState({ name: '', duration_hours: '', price_ksh: '', speed_mbps: 10 });
  const [message, setMessage] = useState('');

  const value = (plan, field) => (drafts[plan.id]?.[field] ?? plan[field]);
  const edit = (id, field, val) => setDrafts((d) => ({ ...d, [id]: { ...d[id], [field]: val } }));

  const save = async (plan, extra = {}) => {
    setMessage('');
    try {
      await api(`/voucher-types/${plan.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ ...drafts[plan.id], ...extra })
      });
      setDrafts((d) => ({ ...d, [plan.id]: undefined }));
      onChange();
    } catch (error) {
      setMessage(error.message);
    }
  };

  const create = async (e) => {
    e.preventDefault();
    setMessage('');
    try {
      await api('/voucher-types', { method: 'POST', body: JSON.stringify(newPlan) });
      setNewPlan({ name: '', duration_hours: '', price_ksh: '', speed_mbps: 10 });
      onChange();
    } catch (error) {
      setMessage(error.message);
    }
  };

  return (
    <div className="table-container">
      <h2>Voucher Plans</h2>
      <p className="hint">Prices and plans shown on the customer portal. Disabled plans are hidden from customers.</p>
      {message && <div className="error-message">{message}</div>}
      <div className="table-scroll">
        <table className="data-table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Duration (hours)</th>
              <th>Price (KES)</th>
              <th>Speed (Mbps)</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {voucherTypes.map((plan) => (
              <tr key={plan.id}>
                <td><input className="cell-input" value={value(plan, 'name')} onChange={(e) => edit(plan.id, 'name', e.target.value)} /></td>
                <td>
                  <input className="cell-input narrow" type="number" value={value(plan, 'duration_hours')}
                    onChange={(e) => edit(plan.id, 'duration_hours', e.target.value)} />
                  <span className="hint inline">{formatDuration(Number(value(plan, 'duration_hours')) || 1)}</span>
                </td>
                <td><input className="cell-input narrow" type="number" value={value(plan, 'price_ksh')} onChange={(e) => edit(plan.id, 'price_ksh', e.target.value)} /></td>
                <td><input className="cell-input narrow" type="number" value={value(plan, 'speed_mbps')} onChange={(e) => edit(plan.id, 'speed_mbps', e.target.value)} /></td>
                <td>
                  <span className={`badge ${plan.is_active ? 'active' : 'inactive'}`}>
                    {plan.is_active ? 'Live' : 'Disabled'}
                  </span>
                </td>
                <td className="row-actions">
                  {drafts[plan.id] && <button className="btn btn-primary btn-small" onClick={() => save(plan)}>Save</button>}
                  <button className="btn-text small" onClick={() => save(plan, { is_active: !plan.is_active })}>
                    {plan.is_active ? 'Disable' : 'Enable'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 className="spaced">Add Plan</h2>
      <form className="inline-form" onSubmit={create}>
        <input placeholder="Name, e.g. 12 hours" value={newPlan.name} onChange={(e) => setNewPlan({ ...newPlan, name: e.target.value })} required />
        <input type="number" placeholder="Hours" value={newPlan.duration_hours} onChange={(e) => setNewPlan({ ...newPlan, duration_hours: e.target.value })} required />
        <input type="number" placeholder="Price KES" value={newPlan.price_ksh} onChange={(e) => setNewPlan({ ...newPlan, price_ksh: e.target.value })} required />
        <input type="number" placeholder="Mbps" value={newPlan.speed_mbps} onChange={(e) => setNewPlan({ ...newPlan, speed_mbps: e.target.value })} />
        <button type="submit" className="btn btn-primary">Add</button>
      </form>
    </div>
  );
};

export default Dashboard;
