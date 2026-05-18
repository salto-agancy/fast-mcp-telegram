# 📁 Project Structure

## Overview

The fast-mcp-telegram project follows a modular architecture with clear separation of concerns between components.

## Directory Structure

```
fast-mcp-telegram/
├── src/                          # Source code
│   ├── client/                   # Telegram client management
│   │   └── connection.py         # Token management, LRU cache, session isolation
│   ├── config/                   # Configuration and logging
│   │   ├── logging.py            # Logging configuration and diagnostic formatting
│   │   ├── server_config.py      # Server configuration with pydantic
│   │   └── settings.py           # Configuration management with dynamic version reading
│   ├── server_components/        # Server modules (auth, health, tools, web setup)
│   │   ├── auth.py               # Authentication middleware and Bearer token extraction
│   │   ├── auth_middleware.py    # Authentication context decorator
│   │   ├── attachment_routes.py  # File attachment download endpoints
│   │   ├── attachment_tickets.py # Secure attachment ticket management
│   │   ├── bot_restrictions.py   # Bot session restrictions
│   │   ├── errors.py             # Error handling decorators
│   │   ├── health.py             # Health endpoint registrar
│   │   ├── mtproto_api.py        # MTProto API endpoint implementation
│   │   ├── session_token_verifier.py  # Session token verification
│   │   ├── tools_register.py     # Tool registrar
│   │   └── web_setup.py          # Web setup routes registrar
│   ├── templates/                # Web setup interface templates
│   │   ├── base.html             # Base template
│   │   ├── setup.html            # Main setup page
│   │   └── fragments/            # HTMX form fragments
│   │       ├── 2fa_form.html     # 2FA authentication form
│   │       ├── code_form.html    # Verification code form
│   │       └── config.html       # Configuration generation
│   ├── tools/                    # MCP tool implementations
│   │   ├── chat_discovery/       # Find chats, folders, global search, chat info
│   │   ├── links.py              # Telegram link generation
│   │   ├── messages/             # Message operations module
│   │   │   ├── __init__.py
│   │   │   ├── core.py
│   │   │   ├── editing.py
│   │   │   ├── file_handling.py
│   │   │   ├── phone.py
│   │   │   ├── reading.py
│   │   │   └── sending.py
│   │   ├── mtproto.py            # Direct MTProto API access
│   │   └── search/               # get_messages (search, browse, IDs, replies)
│   │       ├── __init__.py
│   │       ├── core.py
│   │       ├── types.py
│   │       ├── results.py
│   │       ├── replies.py
│   │       ├── forum_replies.py
│   │       ├── search_mode.py
│   │       ├── search_generators.py
│   │       └── topic_search.py
│   ├── utils/                    # Utility functions
│   │   ├── discussion.py         # Discussion group utilities
│   │   ├── entity.py             # Entity resolution and formatting
│   │   ├── error_handling.py     # Error management and structured responses
│   │   ├── helpers.py            # General utility functions
│   │   ├── logging_utils.py      # Consolidated logging utilities
│   │   ├── mcp_config.py         # MCP configuration utilities
│   │   └── message_format.py     # Message formatting and media parsing
│   ├── cli_setup.py              # CLI setup with pydantic-settings
│   └── server.py                 # Main server entry point
├── tests/                        # Test suite
│   ├── __init__.py               # Tests package initialization
│   ├── conftest.py               # Shared fixtures and configuration
│   ├── test_*.py                 # Organized test modules by functionality
│   └── README.md                 # Project documentation (this file)
├── docs/                         # Documentation
│   ├── Installation.md           # Installation and remote deployment guide
│   ├── MTProto-Bridge.md         # MTProto HTTP endpoint documentation
│   ├── Tools-Reference.md        # Complete tools reference
│   ├── Search-Guidelines.md      # Search best practices
│   └── Project-Structure.md      # This file
├── scripts/                      # Deployment and utility scripts
│   ├── sync-remote-config.sh     # Remote config synchronization script
│   └── check-status.sh           # Health status check script
├── .env.example                  # Environment template
├── docker-compose.yml            # Docker configuration
├── Dockerfile                    # Container build
├── pyproject.toml                # Project configuration
├── SECURITY.md                   # Security and authentication guide
├── CONTRIBUTING.md               # Contributing guidelines
└── README.md                     # Main project documentation
```

## Core Components

### Server Entry Point
- **`src/server.py`**: Main MCP server entry point
  - Registers routes and tools on startup
  - Configures FastMCP with appropriate transport
  - Handles authentication middleware

### Client Management
- **`src/client/connection.py`**: Telegram client management
  - Token-based session isolation
  - LRU cache management
  - Automatic session cleanup
  - Connection pooling and error handling

### Configuration System
- **`src/config/settings.py`**: Centralized configuration
  - Dynamic version reading from pyproject.toml
  - Three server modes (stdio, http-no-auth, http-auth)
  - Environment variable management
- **`src/config/logging.py`**: Logging configuration
  - Loguru integration with stdlib bridge
  - Structured logging with parameter sanitization
  - Performance-optimized logging levels

### Server Components
- **`src/server_components/auth.py`**: Authentication middleware
  - Bearer token extraction and validation
  - Request-scoped authentication context
  - Session isolation and management
- **`src/server_components/auth_middleware.py`**: Authentication context decorator
  - Auth context management for tool execution
- **`src/server_components/health.py`**: Health monitoring
  - Health endpoint registration
  - Session statistics and monitoring
  - Container health checks
- **`src/server_components/mtproto_api.py`**: MTProto HTTP endpoint
  - Direct Telegram API access via HTTP
  - Entity resolution and safety guardrails
  - Case-insensitive method resolution
- **`src/server_components/tools_register.py`**: Tool registration
  - FastMCP tool registration with MCP ToolAnnotations
  - Authentication decorator application
  - Tool discovery and registration with behavioral hints
- **`src/server_components/web_setup.py`**: Web setup interface
  - HTMX-based authentication and reauthorization flow
  - Token-based reauthorization with security validation
  - Session management and cleanup
  - Configuration generation and download
  - Phone verification for reauthorization
- **`src/server_components/attachment_routes.py`**: File attachment endpoints
  - Secure file attachment download routes
- **`src/server_components/attachment_tickets.py`**: Attachment ticket management
  - Secure ticket generation and validation for attachments
- **`src/server_components/bot_restrictions.py`**: Bot session restrictions
  - Limitations for bot-operated sessions
- **`src/server_components/errors.py`**: Error handling decorators
  - Standardized error handling for tools
- **`src/server_components/session_token_verifier.py`**: Session token verification
  - Token validation utilities

### Tool Implementations
- **`src/tools/search/`**: `get_messages` implementation
  - `core.py`: mode dispatch and `search_messages_impl`
  - `search_mode.py`: per-chat/global query and browse
  - `replies.py` / `forum_replies.py`: reply_to_id and forum in-topic paths
  - `topic_search.py`: shared SearchRequest builder for topic-scoped search
  - `results.py`: message dict building for listed results
- **`src/tools/messages/`**: Message operations module
  - `core.py`: Core message functionality
  - `sending.py`: Send messages with files and formatting
  - `editing.py`: Edit existing messages
  - `reading.py`: Read and retrieve messages
  - `file_handling.py`: File upload and attachment handling
  - `phone.py`: Send messages to phone numbers
- **`src/tools/chat_discovery/`**: Chat discovery and metadata
  - `find_chats` / folder filters / date-bounded dialog search
  - Global Telegram entity search
  - `get_chat_info` implementation (forum topics, enriched profiles)
- **`src/tools/links.py`**: Link generation
  - Telegram link generation
  - Message link formatting
  - Entity link resolution
- **`src/tools/mtproto.py`**: Direct API access
  - Comprehensive MTProto method invocation with enhanced features
  - Method name normalization and dangerous method protection
  - Entity resolution and parameter sanitization
  - Single unified function architecture for both MCP tool and HTTP bridge
  - Response formatting and JSON-safe conversion

### Utility Functions
- **`src/utils/entity.py`**: Entity resolution
  - Chat ID format normalization
  - Entity resolution from various formats
  - Uniform entity schema formatting
- **`src/utils/error_handling.py`**: Error management
  - Structured error responses
  - Error type classification
  - Parameter sanitization for logging
- **`src/utils/helpers.py`**: General utilities
  - Method name normalization
  - Parameter validation helpers
  - Common utility functions
- **`src/utils/logging_utils.py`**: Logging utilities
  - Consolidated logging functions
  - Parameter sanitization and enhancement
  - Request tracking and correlation
- **`src/utils/message_format.py`**: Message formatting, interactive media parsing (Todo lists, polls), and voice transcription
  - Message content formatting
  - Media placeholder generation
  - Link generation and formatting
- **`src/utils/mcp_config.py`**: MCP configuration utilities
  - MCP server configuration helpers

## Web Interface

### Templates
- **`src/templates/base.html`**: Base template
  - Common HTML structure
  - CSS and JavaScript includes
  - Responsive design framework
- **`src/templates/setup.html`**: Main setup page
  - Authentication flow container
  - Progress indication
  - Error display and recovery
- **`src/templates/fragments/`**: HTMX fragments
  - Modular form components
  - Dynamic form updates
  - Progressive disclosure

### Setup Flow
1. **Mode selection**: Choose new session or reauthorize existing
2. **Phone submission**: User enters phone number (new or reauth)
3. **Token validation**: For reauth, validate existing bearer token
4. **Code verification**: User enters verification code
5. **2FA handling**: Optional two-factor authentication
6. **Session management**: New session created or existing reauthorized
7. **Config generation**: Bearer token and MCP configuration
8. **Download**: Ready-to-use configuration file

## Testing Infrastructure

### Test Organization
- **`tests/conftest.py`**: Shared fixtures and configuration
  - Mock Telegram client setup
  - Test server configuration
  - Common test utilities
- **`tests/test_*.py`**: Organized test modules
  - Unit tests for individual functions
  - Integration tests for MCP tools
  - Authentication and security tests

### Test Categories
- **Basic functionality**: Core MCP tool operations
- **Authentication**: Bearer token and session management
- **Error handling**: Structured error responses
- **Security**: File handling and SSRF protection
- **Performance**: Async operations and caching

## Deployment Files

### Docker Configuration
- **`Dockerfile`**: Multi-stage container build
  - Optimized pip-based installation
  - Proper user permissions
  - Session directory setup
- **`docker-compose.yml`**: Production configuration
  - Traefik integration
  - Health checks
  - Volume mounting
  - Environment configuration

### Deployment Scripts
- **`scripts/sync-remote-config.sh`**: Remote config synchronization
  - Session backup and restore
  - Docker image pulling
  - Service restart
  - Error handling and logging
- **`scripts/check-status.sh`**: Health status check script

## Configuration Management

### Environment Variables
- **`.env.example`**: Template for environment configuration
- **`.env`**: Local environment variables (git-ignored)
- **Docker environment**: Container-specific configuration

### Project Configuration
- **`pyproject.toml`**: Project metadata and dependencies
  - Package configuration
  - Dependency management
  - Build system configuration
- **`pytest.ini`**: Test configuration
  - Test discovery patterns
  - Coverage settings
  - Async test configuration

## Documentation Structure

### Public Documentation
- **`README.md`**: Main project documentation
- **`docs/`**: Detailed guides and references
- **`SECURITY.md`**: Security considerations
- **`CONTRIBUTING.md`**: Development guidelines

### Internal Documentation
- **`memory-bank/`**: Project knowledge base
  - Architectural decisions
  - Development context
  - Progress tracking
  - Technical patterns

## Session Management

### Session Storage
- **Location**: `~/.config/fast-mcp-telegram/`
- **Format**: `{token}.session` for multi-user isolation
- **Permissions**: Automatic management (1000:1000)
- **Backup**: Automatic backup before deployments
- **Restore**: Automatic restore after deployments

### Session Lifecycle
1. **Creation**: New session on first authentication
2. **Activation**: Session loaded into memory
3. **Usage**: Session used for API calls
4. **Eviction**: LRU-based removal from memory
5. **Cleanup**: Invalid session deletion

## Security Considerations

### File Security
- **Session files**: Excluded from version control
- **Environment variables**: Never committed
- **SSRF protection**: URL validation and blocking
- **File size limits**: Configurable download limits

### Authentication
- **Bearer tokens**: Cryptographically secure
- **Session isolation**: Per-token session files
- **Token rotation**: Regular token updates
- **Access monitoring**: Health endpoint tracking

## Performance Optimizations

### Async Operations
- **Parallel execution**: Multi-query searches
- **Connection pooling**: Efficient Telegram client usage
- **Result caching**: In-memory session cache
- **Lazy loading**: On-demand session activation

### Resource Management
- **LRU cache**: Automatic session eviction
- **Memory optimization**: Async generators for large results
- **Log level control**: Reduced logging in production
- **Batch processing**: Efficient API call batching
