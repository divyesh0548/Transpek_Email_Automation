/**
 * Minimal formDetails + clientmachinedetails for email approval/rejection POSTs.
 * OS detection matches templates/old_job_card_email_approve.html (getOperatingSystem + isWindows11).
 */
(function (global) {
    'use strict';

    function parseBrowser(ua) {
        var name = 'Unknown';
        var version = 'Unknown';
        if (/Edg(?:e|)\/([\d.]+)/i.test(ua)) {
            name = 'Edge';
            version = RegExp.$1.split('.')[0];
        } else if (/OPR\/([\d.]+)/i.test(ua)) {
            name = 'Opera';
            version = RegExp.$1.split('.')[0];
        } else if (/CriOS\/([\d.]+)/i.test(ua)) {
            name = 'Chrome';
            version = RegExp.$1.split('.')[0];
        } else if (/Chrome\/([\d.]+)/i.test(ua) && !/Edg/i.test(ua)) {
            name = 'Chrome';
            version = RegExp.$1.split('.')[0];
        } else if (/Firefox\/([\d.]+)/i.test(ua)) {
            name = 'Firefox';
            version = RegExp.$1.split('.')[0];
        } else if (/Version\/([\d.]+).*Safari/i.test(ua)) {
            name = 'Safari';
            version = RegExp.$1.split('.')[0];
        } else if (/Safari/i.test(ua) && !/Chrome/i.test(ua)) {
            name = 'Safari';
        }
        return { name: name, version: version };
    }

    /** Ported from old_job_card_email_approve.html */
    function isWindows11() {
        var userAgent = navigator.userAgent;

        if (userAgent.indexOf('Win64') > -1 && userAgent.indexOf('Edg') > -1) {
            return true;
        }

        try {
            var canvas = document.createElement('canvas');
            var gl = canvas.getContext('webgl') || canvas.getContext('experimental-webgl');
            if (gl) {
                var debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
                if (debugInfo) {
                    gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL);
                }
            }
        } catch (e) {}

        var chromeMatch = userAgent.match(/Chrome\/([\d]+)/);
        if (chromeMatch) {
            var chromeVersion = parseInt(chromeMatch[1], 10);
            if (chromeVersion >= 87) {
                return true;
            }
        }

        if (navigator.userAgentData) {
            try {
                return navigator.userAgentData.platform === 'Windows';
            } catch (e) {}
        }

        return false;
    }

    /** Ported from old_job_card_email_approve.html — return shape trimmed for API (no userAgent). */
    function getOperatingSystem() {
        var userAgent = navigator.userAgent;
        var osName = 'Unknown';
        var osVersion = 'Unknown';
        var osArchitecture = 'Unknown';

        if (userAgent.indexOf('Win') > -1) {
            osName = 'Windows';

            if (userAgent.indexOf('Windows NT 10.0') > -1) {
                if (isWindows11()) {
                    osVersion = '11';
                } else {
                    osVersion = '10';
                }

                if (userAgent.indexOf('Win64') > -1 || userAgent.indexOf('x64') > -1) {
                    osArchitecture = '64-bit';
                } else {
                    osArchitecture = '32-bit';
                }
            } else if (userAgent.indexOf('Windows NT 6.3') > -1) osVersion = '8.1';
            else if (userAgent.indexOf('Windows NT 6.2') > -1) osVersion = '8';
            else if (userAgent.indexOf('Windows NT 6.1') > -1) osVersion = '7';
            else if (userAgent.indexOf('Windows NT 6.0') > -1) osVersion = 'Vista';
            else if (userAgent.indexOf('Windows NT 5.1') > -1) osVersion = 'XP';
        } else if (userAgent.indexOf('Mac') > -1 && userAgent.indexOf('iPhone') === -1 && userAgent.indexOf('iPad') === -1) {
            osName = 'macOS';
            var matchMac = userAgent.match(/Mac OS X ([\d._]+)/);
            if (matchMac) {
                osVersion = matchMac[1].replace(/_/g, '.');
                var versionNum = parseFloat(osVersion);
                if (versionNum >= 14) osVersion += ' (Sonoma or later)';
                else if (versionNum >= 13) osVersion += ' (Ventura)';
                else if (versionNum >= 12) osVersion += ' (Monterey)';
                else if (versionNum >= 11) osVersion += ' (Big Sur)';
                else if (versionNum >= 10.15) osVersion += ' (Catalina)';
            }
            osArchitecture = userAgent.indexOf('Intel') > -1 ? 'Intel' : (userAgent.indexOf('arm64') > -1 ? 'Apple Silicon (M1/M2)' : 'Unknown');
        } else if (userAgent.indexOf('Android') > -1) {
            osName = 'Android';
            var matchAndroid = userAgent.match(/Android ([\d.]+)/);
            if (matchAndroid) osVersion = matchAndroid[1];
            osArchitecture = 'Mobile';
        } else if (userAgent.indexOf('Linux') > -1) {
            osName = 'Linux';

            if (userAgent.indexOf('Ubuntu') > -1) osVersion = 'Ubuntu';
            else if (userAgent.indexOf('Debian') > -1) osVersion = 'Debian';
            else if (userAgent.indexOf('Fedora') > -1) osVersion = 'Fedora';
            else if (userAgent.indexOf('CentOS') > -1) osVersion = 'CentOS';
            else if (userAgent.indexOf('Arch') > -1) osVersion = 'Arch Linux';
            else if (userAgent.indexOf('Mint') > -1) osVersion = 'Linux Mint';
            else osVersion = 'Generic Linux';

            osArchitecture = userAgent.indexOf('x86_64') > -1 ? '64-bit' : (userAgent.indexOf('i686') > -1 ? '32-bit' : 'Unknown');
        } else if (userAgent.indexOf('iPhone') > -1 || userAgent.indexOf('iPad') > -1) {
            if (userAgent.indexOf('iPad') > -1) {
                osName = 'iPadOS';
            } else {
                osName = 'iOS';
            }
            var matchIos = userAgent.match(/OS ([\d_]+)/);
            if (matchIos) osVersion = matchIos[1].replace(/_/g, '.');
            osArchitecture = 'Mobile';
        } else if (userAgent.indexOf('X11') > -1) {
            osName = 'UNIX';
            osArchitecture = userAgent.indexOf('x86_64') > -1 ? '64-bit' : '32-bit';
        }

        return {
            name: osName,
            version: osVersion,
            architecture: osArchitecture
        };
    }

    async function fetchPublicIp() {
        try {
            var res = await fetch('https://api.ipify.org?format=json');
            var j = await res.json();
            if (j && j.ip) return j.ip;
        } catch (e) {}
        return 'Not available';
    }

    /**
     * @returns {Promise<{ formDetails: object, clientmachinedetails: object }>}
     */
    async function buildApprovalTelemetry() {
        var ua = navigator.userAgent || '';
        var ipAddress = await fetchPublicIp();
        var conn = navigator.connection || {};
        var operatingSystem = getOperatingSystem();

        var formDetails = {
            ipAddress: ipAddress,
            device: {
                screenWidth: window.screen ? window.screen.width : null,
                screenHeight: window.screen ? window.screen.height : null
            },
            network: {
                effectiveType: conn.effectiveType || 'unknown'
            }
        };
        var clientmachinedetails = {
            browserName: parseBrowser(ua),
            operatingSystem: operatingSystem
        };
        return { formDetails: formDetails, clientmachinedetails: clientmachinedetails };
    }

    global.buildApprovalTelemetry = buildApprovalTelemetry;
})(typeof window !== 'undefined' ? window : this);
