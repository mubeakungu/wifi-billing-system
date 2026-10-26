/**
 * Step-by-step setup for each router / firewall brand.
 * Values in {braces} are filled in with this system's details:
 *   {server} RADIUS server address   {auth} / {acct} ports   {secret} shared secret
 *   {portal} customer portal URL     {hosts} portal/API host names
 * Menu names move between firmware versions; the settings themselves don't.
 */

export const VENDORS = [
  ['mikrotik', 'MikroTik (RouterOS hotspot)'],
  ['unifi', 'Ubiquiti UniFi'],
  ['omada', 'TP-Link Omada (controller or gateway)'],
  ['pfsense', 'pfSense'],
  ['opnsense', 'OPNsense'],
  ['openwrt', 'OpenWrt (CoovaChilli / openNDS)'],
  ['fortigate', 'Fortinet FortiGate'],
  ['cisco', 'Cisco / Meraki'],
  ['ruckus', 'Ruckus / Aruba / Cambium / Grandstream'],
  ['other', 'Other RADIUS-capable device']
];

export const vendorLabel = (key) => (VENDORS.find(([k]) => k === key) || [key, key])[1];

const COMMON_LOGIN =
  'Customers log in with the voucher code as the username. If the page also asks for a password, ' +
  'they type the code again (or leave it empty).';

export const GUIDES = {
  mikrotik: {
    steps: [
      'Set up the hotspot if you haven\'t: IP > Hotspot > Hotspot Setup.',
      'Paste into Winbox > New Terminal:\n' +
        '/radius add service=hotspot address={server} secret="{secret}" authentication-port={auth} accounting-port={acct} timeout=3s\n' +
        '/ip hotspot profile set [find] use-radius=yes radius-accounting=yes radius-interim-update=5m\n' +
        '{mikrotikWalledGarden}',
      'Optional: in the Routers tab of this dashboard, download the branded login page and upload it to Files > hotspot > login.html. It has a "Buy with M-Pesa" button.',
      COMMON_LOGIN + ' Speed limits are applied automatically (Mikrotik-Rate-Limit).'
    ]
  },
  unifi: {
    steps: [
      'In UniFi Network: Settings > Profiles > RADIUS > Create New. Authentication server: {server}, port {auth}, secret {secret}. Turn on "Enable RADIUS Accounting" with server {server}, port {acct}, interim update 300 seconds.',
      'Open the guest WiFi network and turn on Hotspot / Guest Portal. Under authentication choose RADIUS and select the profile you just made (in newer versions: Insights > Hotspot > Portal > Authentication).',
      'Under the portal\'s "Pre-Authorization Allowances" (allowed hosts), add: {hosts}.',
      'In the portal text or "Promotional URL" add a link to {portal} so customers can buy a voucher.',
      COMMON_LOGIN + ' Session time comes from the voucher. If speeds aren\'t limited, set a bandwidth limit on the guest network.'
    ]
  },
  omada: {
    steps: [
      'In the Omada Controller: Settings > Authentication > Portal > Create New Portal. Pick your guest SSID or network.',
      'Authentication Type: "RADIUS Server" (External RADIUS Server). RADIUS server IP {server}, port {auth}, secret {secret}. Enable RADIUS Accounting with port {acct} and interim update 300 seconds. Authentication mode PAP.',
      'Portal page: "Local Web Portal". Add a link to {portal} in the welcome text so customers can buy a voucher.',
      'Settings > Authentication > Access Control > Pre-Authentication Access: add {hosts}.',
      COMMON_LOGIN + ' Speed limits use WISPr attributes where the firmware supports them.'
    ]
  },
  pfsense: {
    steps: [
      'Services > Captive Portal > Add zone (or edit yours) on the guest interface.',
      'Authentication method: "Use RADIUS authentication". RADIUS protocol: PAP. Primary server: {server}, port {auth}, secret {secret}.',
      'Accounting: tick "Send RADIUS accounting packets", port {acct}, accounting updates "Interim". Tick "Use RADIUS Session-Timeout attributes" and "Per-user bandwidth restriction" with "Use RADIUS WISPr-Bandwidth-Max-Up/Down".',
      'Allowed Hostnames tab: add {hosts}.',
      'Edit the portal page contents to include a link to {portal}. ' + COMMON_LOGIN
    ]
  },
  opnsense: {
    steps: [
      'System > Access > Servers > Add: type RADIUS, hostname {server}, shared secret {secret}, services offered "Authentication" and "Accounting", auth port {auth}, accounting port {acct}, protocol PAP.',
      'Services > Captive Portal > Administration > add zone on the guest interface, "Authenticate using" the RADIUS server you added. Enable "Concurrent user logins" off.',
      'In the zone\'s "Allowed addresses / hostnames" add {hosts}.',
      'Upload a portal template containing a link to {portal}. ' + COMMON_LOGIN
    ]
  },
  openwrt: {
    steps: [
      'Install CoovaChilli (or openNDS with its RADIUS option) on the router.',
      'CoovaChilli settings (/etc/config/chilli or defaults):\n' +
        'HS_RADIUS={server}\nHS_RADIUS2={server}\nHS_RADSECRET={secret}\nHS_RADAUTH={auth}\nHS_RADACCT={acct}\nHS_UAMALLOW={hosts}',
      'Add a link to {portal} on the login page. ' + COMMON_LOGIN + ' Speed limits use ChilliSpot / WISPr attributes.'
    ]
  },
  fortigate: {
    steps: [
      'User & Authentication > RADIUS Servers > Create New: IP {server}, secret {secret}, authentication method PAP. Enable accounting to {server} port {acct}.',
      'User & Authentication > User Groups: create a Firewall group with the RADIUS server as remote server.',
      'On the guest interface or SSID: Security mode "Captive Portal", user groups = that group, exempt destinations {hosts}.',
      COMMON_LOGIN + ' Session time comes from the voucher (Session-Timeout).'
    ]
  },
  cisco: {
    steps: [
      'Add a RADIUS server {server} (auth {auth}, accounting {acct}) with shared secret {secret}.',
      'On Meraki: Wireless > Access control > Splash page "Sign-on with my RADIUS server", add the server and enable RADIUS accounting. Add {hosts} to the walled garden.',
      'On Cisco WLC / Catalyst: create a Web Auth (local web authentication) WLAN using that RADIUS server, and a pre-auth ACL allowing {hosts}.',
      COMMON_LOGIN
    ]
  },
  ruckus: {
    steps: [
      'In the controller, add an AAA / RADIUS server: {server}, auth port {auth}, accounting port {acct}, secret {secret}.',
      'Create a guest/hotspot (captive portal) WLAN that authenticates with that RADIUS server and sends accounting.',
      'Add {hosts} to the walled garden / pre-authentication allow list, and link to {portal} on the portal page.',
      COMMON_LOGIN
    ]
  },
  other: {
    steps: [
      'Any hotspot or captive portal that supports an external RADIUS server works. Use server {server}, authentication port {auth}, accounting port {acct}, shared secret {secret}, PAP (or CHAP), accounting on with interim updates every 5 minutes.',
      'Allow {hosts} before login (walled garden / pre-auth / allowed hosts) and link to {portal} from the login page.',
      COMMON_LOGIN
    ]
  }
};

export const NO_RADIUS_HELP =
  'Home and small-office routers (most Tenda, TP-Link Archer/Deco, Huawei, ZTE and ISP routers) have no hotspot ' +
  'or RADIUS support, so no billing system can control them directly. Keep them as access points: put a small ' +
  'hotspot gateway between your internet line and them, e.g. a MikroTik hAP lite/ax lite, an OpenWrt router or a ' +
  'pfSense/OPNsense box, and add that gateway here. Set the Tenda/TP-Link to Access Point (AP) mode with DHCP off, ' +
  'so all customers pass through the gateway.';

export const fillGuide = (text, values) =>
  text.replace(/\{(\w+)\}/g, (m, key) => (values[key] !== undefined ? values[key] : m));
