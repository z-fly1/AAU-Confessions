/**
 * AAU Confessions Chat App
 * Telegram Mini App for chat management
 */

// ==================== CONFIGURATION ====================
const API_BASE_URL = 'https://aau-confessions.onrender.com/api'; // Change this to your deployed API URL
const POLL_INTERVAL = 3000; // Poll for new messages every 3 seconds
const MAX_MESSAGE_LENGTH = 4000;

// ==================== STATE ====================
let state = {
    user: null,
    initData: null,
    chats: [],
    currentChat: null,
    messages: [],
    pollingInterval: null,
    isLoading: false
};

// ==================== DOM ELEMENTS ====================
const elements = {
    loadingScreen: document.getElementById('loading-screen'),
    app: document.getElementById('app'),
    chatList: document.getElementById('chat-list'),
    emptyChats: document.getElementById('empty-chats'),
    conversationPanel: document.getElementById('conversation-panel'),
    welcomeState: document.getElementById('welcome-state'),
    activeConversation: document.getElementById('active-conversation'),
    messagesContainer: document.getElementById('messages-container'),
    emptyMessages: document.getElementById('empty-messages'),
    partnerEmoji: document.getElementById('partner-emoji'),
    partnerName: document.getElementById('partner-name'),
    partnerStatus: document.getElementById('partner-status'),
    messageInput: document.getElementById('message-input'),
    sendBtn: document.getElementById('send-btn'),
    refreshBtn: document.getElementById('refresh-btn'),
    blockBtn: document.getElementById('block-btn'),
    backBtn: document.getElementById('back-btn'),
    errorToast: document.getElementById('error-toast'),
    errorMessage: document.getElementById('error-message'),
    chatListPanel: document.querySelector('.chat-list-panel')
};

// ==================== UTILITY FUNCTIONS ====================

/**
 * Show error toast
 */
function showError(message) {
    elements.errorMessage.textContent = message;
    elements.errorToast.style.display = 'block';

    setTimeout(() => {
        elements.errorToast.style.display = 'none';
    }, 3000);
}

/**
 * Format timestamp
 */
function formatTime(isoString) {
    const date = new Date(isoString);
    const now = new Date();
    const diff = now - date;

    // Less than 1 minute
    if (diff < 60000) {
        return 'Just now';
    }

    // Less than 1 hour
    if (diff < 3600000) {
        const minutes = Math.floor(diff / 60000);
        return `${minutes}m ago`;
    }

    // Less than 24 hours
    if (diff < 86400000) {
        const hours = Math.floor(diff / 3600000);
        return `${hours}h ago`;
    }

    // Same year
    if (date.getFullYear() === now.getFullYear()) {
        return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    }

    // Different year
    return date.toLocaleDateString('en-US', { year: 'numeric', month: 'short', day: 'numeric' });
}

/**
 * Format message timestamp
 */
function formatMessageTime(isoString) {
    const date = new Date(isoString);
    return date.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
}

/**
 * Escape HTML
 */
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

/**
 * Truncate text
 */
function truncate(text, maxLength) {
    if (text.length <= maxLength) return text;
    return text.substring(0, maxLength) + '...';
}

// ==================== API FUNCTIONS ====================

/**
 * Make authenticated API request
 */
async function apiRequest(endpoint, options = {}) {
    const headers = {
        'Content-Type': 'application/json',
        'Authorization': `twa ${state.initData}`,
        ...options.headers
    };

    try {
        const response = await fetch(`${API_BASE_URL}${endpoint}`, {
            ...options,
            headers
        });

        if (!response.ok) {
            const error = await response.json().catch(() => ({ error: 'Request failed' }));
            throw new Error(error.error || `HTTP ${response.status}`);
        }

        return await response.json();
    } catch (error) {
        console.error('API request failed:', error);
        throw error;
    }
}

/**
 * Load chats from API
 */
async function loadChats() {
    try {
        const data = await apiRequest('/chats');
        state.chats = data.chats || [];
        renderChats();
    } catch (error) {
        showError('Failed to load chats');
        console.error('Error loading chats:', error);
    }
}

/**
 * Load messages for a chat
 */
async function loadMessages(partnerId) {
    try {
        const data = await apiRequest(`/chats/${partnerId}/messages?limit=50`);
        state.messages = data.messages || [];
        state.currentChat = {
            partnerId: data.partner.id,
            partnerName: data.partner.name,
            partnerEmoji: data.partner.emoji
        };
        renderMessages();
    } catch (error) {
        showError('Failed to load messages');
        console.error('Error loading messages:', error);
    }
}

/**
 * Send a message
 */
async function sendMessage(text) {
    if (!state.currentChat || state.isLoading) return;

    state.isLoading = true;
    elements.sendBtn.disabled = true;

    try {
        const data = await apiRequest(`/chats/${state.currentChat.partnerId}/messages`, {
            method: 'POST',
            body: JSON.stringify({ text })
        });

        // Add message to state
        state.messages.push(data.message);

        // Clear input
        elements.messageInput.value = '';
        elements.messageInput.style.height = 'auto';

        // Re-render messages
        renderMessages();

        // Scroll to bottom
        scrollToBottom();

    } catch (error) {
        showError('Failed to send message');
        console.error('Error sending message:', error);
    } finally {
        state.isLoading = false;
        elements.sendBtn.disabled = false;
        elements.messageInput.focus();
    }
}

/**
 * Poll for new messages
 */
async function pollMessages() {
    if (!state.currentChat) return;

    try {
        const data = await apiRequest(`/chats/${state.currentChat.partnerId}/messages?limit=50`);
        const newMessages = data.messages || [];

        // Check if there are new messages
        if (newMessages.length > state.messages.length) {
            state.messages = newMessages;
            renderMessages();
            scrollToBottom();
        }
    } catch (error) {
        console.error('Error polling messages:', error);
    }
}

// ==================== RENDER FUNCTIONS ====================

/**
 * Render chat list
 */
function renderChats() {
    elements.chatList.innerHTML = '';

    if (state.chats.length === 0) {
        elements.emptyChats.style.display = 'flex';
        return;
    }

    elements.emptyChats.style.display = 'none';

    state.chats.forEach(chat => {
        const chatItem = document.createElement('div');
        chatItem.className = 'chat-item';
        if (state.currentChat && state.currentChat.partnerId === chat.partnerId) {
            chatItem.classList.add('active');
        }

        const lastMessagePreview = chat.lastMessage
            ? (chat.isOwn ? 'You: ' : '') + truncate(chat.lastMessage, 30)
            : 'No messages yet';

        const unreadBadge = chat.unreadCount > 0
            ? `<span class="unread-badge">${chat.unreadCount}</span>`
            : '';

        const timeStr = chat.lastMessageTime ? `<div class="chat-time">${formatTime(chat.lastMessageTime)}</div>` : '';

        chatItem.innerHTML = `
            <div class="chat-avatar">${chat.partnerEmoji}</div>
            <div class="chat-info">
                <div class="chat-name">${escapeHtml(chat.partnerName)}</div>
                <div class="chat-preview">${escapeHtml(lastMessagePreview)}</div>
            </div>
            <div class="chat-meta">
                ${timeStr}
                ${unreadBadge}
            </div>
        `;

        chatItem.addEventListener('click', () => selectChat(chat.partnerId));

        elements.chatList.appendChild(chatItem);
    });
}

/**
 * Render messages
 */
function renderMessages() {
    elements.messagesContainer.innerHTML = '';

    if (state.messages.length === 0) {
        elements.emptyMessages.style.display = 'flex';
        return;
    }

    elements.emptyMessages.style.display = 'none';

    state.messages.forEach(message => {
        const messageEl = document.createElement('div');
        messageEl.className = `message ${message.isOwn ? 'own' : 'other'}`;

        let messageContent = '';
        if (message.text) {
            messageContent = escapeHtml(message.text);
        } else if (message.hasSticker) {
            messageContent = '<span class="message-placeholder">🎨 Sticker</span>';
        } else if (message.hasAnimation) {
            messageContent = '<span class="message-placeholder">🎬 GIF</span>';
        }

        messageEl.innerHTML = `
            <div class="message-bubble">
                <div class="message-text">${messageContent}</div>
                <div class="message-meta">
                    <span class="message-time">${formatMessageTime(message.timestamp)}</span>
                </div>
            </div>
        `;

        elements.messagesContainer.appendChild(messageEl);
    });
}

/**
 * Scroll to bottom of messages
 */
function scrollToBottom() {
    elements.messagesContainer.scrollTop = elements.messagesContainer.scrollHeight;
}

// ==================== CHAT SELECTION ====================

/**
 * Select a chat
 */
async function selectChat(partnerId) {
    // Stop polling
    if (state.pollingInterval) {
        clearInterval(state.pollingInterval);
    }

    // Load messages
    await loadMessages(partnerId);

    // Update UI
    elements.welcomeState.style.display = 'none';
    elements.activeConversation.style.display = 'flex';
    elements.partnerEmoji.textContent = state.currentChat.partnerEmoji;
    elements.partnerName.textContent = state.currentChat.partnerName;

    // Update active chat in list
    renderChats();

    // Scroll to bottom
    setTimeout(scrollToBottom, 100);

    // Start polling
    state.pollingInterval = setInterval(pollMessages, POLL_INTERVAL);

    // Mobile: show conversation panel
    if (window.innerWidth <= 768) {
        elements.conversationPanel.classList.add('active');
        elements.chatListPanel.classList.add('conversation-active');
    }
}

/**
 * Go back to chat list (mobile)
 */
function goBackToList() {
    // Stop polling
    if (state.pollingInterval) {
        clearInterval(state.pollingInterval);
    }

    // Mobile: hide conversation panel
    elements.conversationPanel.classList.remove('active');
    elements.chatListPanel.classList.remove('conversation-active');

    // Clear current chat
    state.currentChat = null;
    state.messages = [];

    // Update UI
    elements.activeConversation.style.display = 'none';
    elements.welcomeState.style.display = 'flex';

    // Update chat list
    renderChats();
}

// ==================== EVENT HANDLERS ====================

/**
 * Handle message input
 */
elements.messageInput.addEventListener('input', (e) => {
    // Auto-resize textarea
    e.target.style.height = 'auto';
    e.target.style.height = e.target.scrollHeight + 'px';

    // Enable/disable send button
    const text = e.target.value.trim();
    elements.sendBtn.disabled = text.length === 0 || text.length > MAX_MESSAGE_LENGTH;
});

/**
 * Handle send button click
 */
elements.sendBtn.addEventListener('click', () => {
    const text = elements.messageInput.value.trim();
    if (text && text.length <= MAX_MESSAGE_LENGTH) {
        sendMessage(text);
    }
});

/**
 * Handle Enter key (Shift+Enter for newline)
 */
elements.messageInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        const text = e.target.value.trim();
        if (text && text.length <= MAX_MESSAGE_LENGTH) {
            sendMessage(text);
        }
    }
});

/**
 * Handle refresh button
 */
elements.refreshBtn.addEventListener('click', async () => {
    await loadChats();
    if (state.currentChat) {
        await loadMessages(state.currentChat.partnerId);
    }
});

/**
 * Handle block button
 */
elements.blockBtn.addEventListener('click', async () => {
    if (!state.currentChat) return;

    const partnerName = state.currentChat.partnerName;

    // Use Telegram WebApp to show confirm dialog if available
    if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.showConfirm) {
        window.Telegram.WebApp.showConfirm(
            `Block ${partnerName}? You won't be able to send or receive messages from them.`,
            async (confirmed) => {
                if (confirmed) {
                    await blockUser(state.currentChat.partnerId);
                }
            }
        );
    } else {
        // Fallback for non-Telegram environments
        if (confirm(`Block ${partnerName}? You won't be able to send or receive messages from them.`)) {
            await blockUser(state.currentChat.partnerId);
        }
    }
});

/**
 * Block a user
 */
async function blockUser(partnerId) {
    try {
        await apiRequest(`/chats/${partnerId}/block`, {
            method: 'POST'
        });

        // Show success message
        if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.showAlert) {
            window.Telegram.WebApp.showAlert('User blocked successfully');
        } else {
            alert('User blocked successfully');
        }

        // Go back to chat list
        goBackToList();

        // Reload chats to remove blocked chat
        await loadChats();

    } catch (error) {
        showError('Failed to block user');
        console.error('Error blocking user:', error);
    }
}

/**
 * Handle back button (mobile)
 */
elements.backBtn.addEventListener('click', goBackToList);

// ==================== INITIALIZATION ====================

/**
 * Initialize the app
 */
async function init() {
    try {
        // Initialize Telegram WebApp
        if (window.Telegram && window.Telegram.WebApp) {
            const tg = window.Telegram.WebApp;
            tg.ready();
            tg.expand();

            // Get init data
            state.initData = tg.initData;
            state.user = tg.initDataUnsafe.user;

            // Set theme
            document.documentElement.style.setProperty('--tg-theme-bg-color', tg.themeParams.bg_color || '#0f0f0f');
            document.documentElement.style.setProperty('--tg-theme-text-color', tg.themeParams.text_color || '#ffffff');

        } else {
            // For local testing without Telegram
            console.warn('Running outside Telegram WebApp');
            state.initData = 'test_init_data';
            state.user = { id: 123456, first_name: 'Test User' };
        }

        // Load chats
        await loadChats();

        // Hide loading screen
        elements.loadingScreen.style.display = 'none';
        elements.app.style.display = 'flex';

    } catch (error) {
        console.error('Initialization error:', error);
        showError('Failed to initialize app');

        // Still show the app
        elements.loadingScreen.style.display = 'none';
        elements.app.style.display = 'flex';
    }
}

// Start the app when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}

// Cleanup on page unload
window.addEventListener('beforeunload', () => {
    if (state.pollingInterval) {
        clearInterval(state.pollingInterval);
    }
});
