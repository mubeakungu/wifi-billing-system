/**
 * WiFi Billing System - Customer Portal
 * Public page for customers to buy vouchers
 * Auto-triggers STK push on their phone, then polls the backend for the result
 */

import React, { useEffect, useRef, useState } from 'react';
import { apiCall } from './api';
import './CustomerPortal.css';

// Plans come from the backend (Plans tab in the dashboard); these just decorate them in order
const PLAN_ICONS = ['⚡', '☀️', '📅', '📦', '🚀', '📶'];
const PLAN_COLORS = ['#3B82F6', '#10B981', '#F59E0B', '#EF4444', '#8B5CF6', '#EC4899'];

const POLL_INTERVAL_MS = 3000;
const POLL_TIMEOUT_MS = 2 * 60 * 1000;

export const formatDuration = (hours) => {
  if (hours % 720 === 0) return `${hours / 720} month${hours > 720 ? 's' : ''}`;
  if (hours % 168 === 0) return `${hours / 168} week${hours > 168 ? 's' : ''}`;
  if (hours % 24 === 0) return `${hours / 24} day${hours > 24 ? 's' : ''}`;
  return `${hours} hour${hours > 1 ? 's' : ''}`;
};

const toVoucherOption = (type, index) => ({
  id: type.id,
  name: type.name.charAt(0).toUpperCase() + type.name.slice(1),
  icon: PLAN_ICONS[index % PLAN_ICONS.length],
  color: PLAN_COLORS[index % PLAN_COLORS.length],
  duration: formatDuration(type.duration_hours),
  description: `Unlimited browsing for ${formatDuration(type.duration_hours)}`,
  price: type.price_ksh,
  speed: type.speed_mbps
});

// The input holds the 9 digits after +254; a leading 0 or 254 is dropped
const toLocalDigits = (value) => {
  let digits = value.replace(/\D/g, '');
  if (digits.startsWith('254')) digits = digits.slice(3);
  if (digits.startsWith('0')) digits = digits.slice(1);
  return digits.slice(0, 9);
};

const CustomerPortal = ({ onAdminClick }) => {
  const [localPhone, setLocalPhone] = useState('');
  const [voucherTypes, setVoucherTypes] = useState([]);
  const [typesLoaded, setTypesLoaded] = useState(false);
  const [config, setConfig] = useState({ business_name: '', support_contact: '' });
  const [typesError, setTypesError] = useState('');
  const [selectedVoucher, setSelectedVoucher] = useState(null);
  const [status, setStatus] = useState(null); // null, 'processing', 'waiting', 'success', 'error', 'timeout'
  const [transactionId, setTransactionId] = useState(null);
  const [errorMessage, setErrorMessage] = useState('');
  const [result, setResult] = useState(null);
  const [copied, setCopied] = useState(false);
  const pollRef = useRef(null);

  const phone = localPhone ? `254${localPhone}` : '';
  const phoneValid = /^254[17]\d{8}$/.test(phone);
  const busy = status === 'processing' || status === 'waiting';

  const loadVoucherTypes = async () => {
    setTypesError('');
    try {
      const types = await apiCall('/voucher-types');
      setVoucherTypes(types.map(toVoucherOption));
      setTypesLoaded(true);
    } catch (error) {
      setTypesError(error.message);
    }
  };

  useEffect(() => {
    loadVoucherTypes();
    apiCall('/config')
      .then((cfg) => {
        setConfig(cfg);
        if (cfg.business_name) document.title = cfg.business_name;
      })
      .catch(() => {});
    return () => clearTimeout(pollRef.current);
  }, []);

  const formatPhoneForDisplay = (p) =>
    p.length === 12 ? `+${p.slice(0, 3)} ${p.slice(3, 6)} ${p.slice(6, 9)} ${p.slice(9)}` : p;

  const showError = (message) => {
    clearTimeout(pollRef.current);
    setErrorMessage(message);
    setStatus('error');
  };

  const pollStatus = (reference, startedAt) => {
    pollRef.current = setTimeout(async () => {
      try {
        const tx = await apiCall(`/transactions/${reference}/status`);
        if (tx.status === 'completed') {
          setResult(tx);
          setStatus('success');
          return;
        }
        if (tx.status === 'failed') {
          showError(tx.result_desc || 'Payment was not completed.');
          return;
        }
      } catch (error) {
        console.error('Poll error:', error);
      }
      if (Date.now() - startedAt >= POLL_TIMEOUT_MS) {
        setStatus('timeout');
        return;
      }
      pollStatus(reference, startedAt);
    }, POLL_INTERVAL_MS);
  };

  const handleBuyVoucher = async (voucherType) => {
    if (!phoneValid) {
      setErrorMessage('Please enter a valid Safaricom phone number (07XX XXX XXX or 01XX XXX XXX)');
      setStatus('error');
      return;
    }

    clearTimeout(pollRef.current);
    setSelectedVoucher(voucherType);
    setStatus('processing');
    setErrorMessage('');
    setResult(null);

    try {
      // Step 1: Create or get customer (the backend returns the existing one)
      const customer = await apiCall('/customers', {
        method: 'POST',
        body: JSON.stringify({ phone })
      });

      // Step 2: Initiate payment (STK Push)
      const payment = await apiCall('/transactions/initiate', {
        method: 'POST',
        body: JSON.stringify({ customer_id: customer.id, voucher_type_id: voucherType.id })
      });
      setTransactionId(payment.transaction_id);
      setStatus('waiting');

      // Step 3: Poll until the M-Pesa callback marks it completed or failed
      pollStatus(payment.transaction_id, Date.now());
    } catch (error) {
      console.error('Payment error:', error);
      showError(error.message || 'Payment failed. Please try again.');
    }
  };

  const checkAgain = () => {
    if (!transactionId) return;
    setStatus('waiting');
    pollStatus(transactionId, Date.now());
  };

  const reset = () => {
    clearTimeout(pollRef.current);
    setSelectedVoucher(null);
    setStatus(null);
    setTransactionId(null);
    setResult(null);
    setCopied(false);
  };

  const copyCode = async () => {
    try {
      await navigator.clipboard.writeText(result.voucher.code);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className="customer-portal">
      {/* Header */}
      <div className="portal-header">
        <div className="header-content">
          <h1>📶 {config.business_name || 'WiFi'}</h1>
          <p className="tagline">Buy vouchers with M-Pesa in seconds</p>
        </div>
        <button className="admin-link" onClick={onAdminClick}>Admin</button>
      </div>

      {/* Main Content */}
      <div className="portal-container">
        <div className="purchase-card">
          {/* Phone Input */}
          <div className="input-section">
            <h2>Your M-Pesa Phone Number</h2>
            <div className={`phone-input-wrapper ${localPhone && !phoneValid ? 'invalid' : ''}`}>
              <span className="phone-prefix">+254</span>
              <input
                type="tel"
                inputMode="numeric"
                placeholder="7XX XXX XXX"
                value={localPhone}
                onChange={(e) => setLocalPhone(toLocalDigits(e.target.value))}
                disabled={busy}
                className="phone-input"
                aria-label="Phone number"
              />
            </div>
            {phoneValid && <p className="phone-formatted">✓ {formatPhoneForDisplay(phone)}</p>}
            {localPhone && !phoneValid && localPhone.length >= 9 && (
              <p className="phone-hint">Number should start with 7 or 1</p>
            )}
          </div>

          {/* Divider */}
          <div className="divider">
            <span>Select Voucher</span>
          </div>

          {/* Voucher Selection */}
          {typesError ? (
            <div className="status-message error">
              <div className="error-icon">✕</div>
              <p>Could not load voucher plans. {typesError}</p>
              <button className="btn-retry" onClick={loadVoucherTypes}>Retry</button>
            </div>
          ) : (
            <div className="voucher-grid">
              {!typesLoaded && <p className="muted">Loading plans...</p>}
              {typesLoaded && voucherTypes.length === 0 && (
                <p className="muted">No plans are available right now. Please check back soon.</p>
              )}
              {voucherTypes.map((voucher) => {
                const selected = selectedVoucher?.id === voucher.id;
                return (
                  <button
                    key={voucher.id}
                    className={`voucher-card ${selected ? 'selected' : ''}`}
                    onClick={() => handleBuyVoucher(voucher)}
                    disabled={busy || !phoneValid}
                    style={{
                      borderColor: selected ? voucher.color : undefined,
                      backgroundColor: selected ? `${voucher.color}20` : undefined
                    }}
                  >
                    <div className="voucher-icon">{voucher.icon}</div>
                    <div className="voucher-name">{voucher.name}</div>
                    <div className="voucher-duration">{voucher.duration} · {voucher.speed} Mbps</div>
                    <div className="voucher-price" style={{ color: voucher.color }}>KES {voucher.price}</div>
                    <div className="voucher-desc">{voucher.description}</div>

                    {/* Loading indicator */}
                    {busy && selected && <div className="loading-spinner"></div>}
                  </button>
                );
              })}
            </div>
          )}
          {!phoneValid && voucherTypes.length > 0 && !status && (
            <p className="muted center">Enter your phone number to choose a plan</p>
          )}

          {/* Status Messages */}
          {status === 'processing' && (
            <div className="status-message processing">
              <div className="spinner"></div>
              <p>Sending payment request...</p>
            </div>
          )}

          {status === 'waiting' && (
            <div className="status-message waiting">
              <div className="phone-icon">📱</div>
              <h3>Check Your Phone!</h3>
              <p>An M-Pesa prompt for KES {selectedVoucher?.price} has been sent to {formatPhoneForDisplay(phone)}.</p>
              <p className="instruction">Enter your M-Pesa PIN to complete the purchase.</p>
              <div className="progress-bar">
                <div className="progress-fill"></div>
              </div>
              <button className="btn-link" onClick={reset}>Cancel</button>
            </div>
          )}

          {status === 'timeout' && (
            <div className="status-message waiting">
              <div className="phone-icon">⏳</div>
              <h3>Still waiting for M-Pesa</h3>
              <p>We have not received confirmation yet. If you already paid, check again in a moment.</p>
              <div className="button-row">
                <button className="btn-retry" onClick={checkAgain}>Check Again</button>
                <button className="btn-link" onClick={reset}>Start Over</button>
              </div>
            </div>
          )}

          {status === 'success' && result?.voucher && (
            <div className="status-message success">
              <div className="success-icon">✓</div>
              <h3>Payment Successful!</h3>
              <p className="code-label">Your WiFi voucher code</p>
              <button className="voucher-code" onClick={copyCode} title="Copy code">
                {result.voucher.code}
              </button>
              <p className="copy-hint">{copied ? 'Copied!' : 'Tap the code to copy it'}</p>
              <div className="voucher-details">
                <p className="detail-label">Voucher Type:</p>
                <p className="detail-value">{selectedVoucher?.name || result.voucher.type}</p>
                <p className="detail-label">Amount Paid:</p>
                <p className="detail-value">KES {result.amount}</p>
                {result.mpesa_receipt && (
                  <>
                    <p className="detail-label">M-Pesa Receipt:</p>
                    <p className="detail-value">{result.mpesa_receipt}</p>
                  </>
                )}
                <p className="detail-label">Valid Until:</p>
                <p className="detail-value">{new Date(result.voucher.expires_at).toLocaleString()}</p>
              </div>
              <p className="success-message">Enter this code on the WiFi login page to connect.{result.sms_enabled ? ' A copy has been sent to your phone by SMS.' : ' Keep it safe.'}</p>
              <div className="button-row">
                {config.hotspot_login_url && (
                  <a className="btn-retry btn-connect" href={config.hotspot_login_url}>Connect to WiFi</a>
                )}
                <button className="btn-retry" onClick={reset}>Buy Another</button>
              </div>
            </div>
          )}

          {status === 'error' && (
            <div className="status-message error">
              <div className="error-icon">✕</div>
              <p>{errorMessage}</p>
              <button className="btn-retry" onClick={reset}>Try Again</button>
            </div>
          )}
        </div>

        {/* Info Section */}
        <div className="info-section">
          <div className="info-card">
            <h3>🔒 Secure Payment</h3>
            <p>All payments processed through M-Pesa STK Push</p>
          </div>
          <div className="info-card">
            <h3>⚡ Instant Activation</h3>
            <p>Voucher activated immediately after payment</p>
          </div>
          <div className="info-card">
            <h3>🎟️ Code On Screen</h3>
            <p>Your voucher code appears here as soon as you pay</p>
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="portal-footer">
        {config.support_contact && <p>Need help? Contact {config.support_contact}</p>}
        <p className="footer-links">Payments by M-Pesa</p>
      </div>
    </div>
  );
};

export default CustomerPortal;
