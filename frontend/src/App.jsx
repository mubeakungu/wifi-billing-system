/**
 * WiFi Billing System - Main App
 * Routes between the public customer portal and the protected admin dashboard.
 * The admin side lives at #/admin so it can be bookmarked.
 */

import React, { useCallback, useEffect, useState } from 'react';
import CustomerPortal from './CustomerPortal';
import Dashboard, { LoginPage } from './Dashboard';
import { apiCall } from './api';

const readView = () => (window.location.hash.startsWith('#/admin') ? 'admin' : 'customer');

const readAdmin = () => {
  try {
    return JSON.parse(localStorage.getItem('admin') || 'null');
  } catch {
    return null;
  }
};

export default function App() {
  const [view, setView] = useState(readView);
  const [token, setToken] = useState(() => localStorage.getItem('auth_token'));
  const [admin, setAdmin] = useState(readAdmin);

  useEffect(() => {
    const onHash = () => setView(readView());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const go = (next) => {
    window.location.hash = next === 'admin' ? '#/admin' : '#/';
    setView(next);
  };

  const handleLogin = async (username, password) => {
    const data = await apiCall('/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password })
    });
    localStorage.setItem('auth_token', data.token);
    localStorage.setItem('admin', JSON.stringify(data.admin));
    setToken(data.token);
    setAdmin(data.admin);
  };

  const handleLogout = useCallback(() => {
    localStorage.removeItem('auth_token');
    localStorage.removeItem('admin');
    setToken(null);
    setAdmin(null);
  }, []);

  // Customer Portal View (public)
  if (view === 'customer') {
    return <CustomerPortal onAdminClick={() => go('admin')} />;
  }

  // Admin Dashboard View (protected)
  if (!token || !admin) {
    return <LoginPage onLogin={handleLogin} onBack={() => go('customer')} />;
  }

  return (
    <Dashboard
      token={token}
      admin={admin}
      onLogout={handleLogout}
      onSwitchToCustomer={() => go('customer')}
    />
  );
}
