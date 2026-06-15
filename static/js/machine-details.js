/**
 * Client-side user machine details collection
 * This script collects comprehensive machine details from the browser
 */

function collectClientMachineDetails() {
    const details = {
        timestamp: new Date().toISOString(),
        vendor: navigator.vendor || 'Unknown',
        isMobile: /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent),
        browserName: getBrowserName(),
        operatingSystem: getOperatingSystem()
    };
    
    return details;
}

// Simple browser detection
function getBrowserName() {
    const ua = navigator.userAgent;
    if (ua.indexOf('Chrome') > -1) return 'Chrome';
    if (ua.indexOf('Firefox') > -1) return 'Firefox';
    if (ua.indexOf('Safari') > -1) return 'Safari';
    if (ua.indexOf('Edge') > -1) return 'Edge';
    if (ua.indexOf('Opera') > -1) return 'Opera';
    return 'Unknown';
}

// Simple OS detection
function getOperatingSystem() {
    const ua = navigator.userAgent;
    if (ua.indexOf('Windows') > -1) return 'Windows';
    if (ua.indexOf('Mac') > -1) return 'macOS';
    if (ua.indexOf('Linux') > -1) return 'Linux';
    if (ua.indexOf('Android') > -1) return 'Android';
    if (ua.indexOf('iPhone') > -1 || ua.indexOf('iPad') > -1) return 'iOS';
    return 'Unknown';
}

function sendMachineDetailsWithForm(formData) {
    const machineDetails = collectClientMachineDetails();
    
    // Add machine details to form data
    if (formData instanceof FormData) {
        formData.append('client_machine_details', JSON.stringify(machineDetails));
    } else {
        // If it's a regular form, create hidden input
        const hiddenInput = document.createElement('input');
        hiddenInput.type = 'hidden';
        hiddenInput.name = 'client_machine_details';
        hiddenInput.value = JSON.stringify(machineDetails);
        
        // Find the form and append the input
        const form = document.querySelector('form');
        if (form) {
            form.appendChild(hiddenInput);
        }
    }
    
    return machineDetails;
}

// Auto-attach to forms when DOM is ready
document.addEventListener('DOMContentLoaded', function() {
    // Attach to all forms that don't have the 'no-machine-details' class
    const forms = document.querySelectorAll('form:not(.no-machine-details)');
    
    forms.forEach(function(form) {
        form.addEventListener('submit', function(e) {
            // Only add machine details if not already present
            const existingInput = form.querySelector('input[name="client_machine_details"]');
            if (!existingInput) {
                const machineDetails = collectClientMachineDetails();
                
                const hiddenInput = document.createElement('input');
                hiddenInput.type = 'hidden';
                hiddenInput.name = 'client_machine_details';
                hiddenInput.value = JSON.stringify(machineDetails);
                
                form.appendChild(hiddenInput);
            }
        });
    });
});

// Export functions for manual use
window.collectClientMachineDetails = collectClientMachineDetails;
window.sendMachineDetailsWithForm = sendMachineDetailsWithForm;
