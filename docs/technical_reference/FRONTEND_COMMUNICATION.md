# OpenGrowBox Frontend Communication Architecture

This document describes how the frontend (ogb-ha-gui) communicates with the Home Assistant integration (ogb-ha-backend) and the premium API (ogb-grow-api).

## Overview

```
+-------------------+     +-------------------+     +-------------------+
|   ogb-ha-gui      |     |   Home Assistant  |     |   ogb-grow-api    |
|   (React/Vite)    |     |   + ogb-ha-backend|     |   (Premium API)   |
+-------------------+     +-------------------+     +-------------------+
         |                         |                         |
         |   WebSocket (HA)        |                         |
         |<----------------------->|                         |
         |                         |                         |
         |   fire_event            |   WebSocket/REST        |
         |------------------------>|------------------------>|
         |                         |                         |
         |   subscribe_events      |   Response              |
         |<------------------------|<------------------------|
         |                         |                         |
```

## Communication Layers

### Layer 1: Frontend <-> Home Assistant

The frontend connects to Home Assistant using the `home-assistant-js-websocket` library.

**Connection Setup:**
```javascript
import { createConnection, createLongLivedTokenAuth } from 'home-assistant-js-websocket';

const auth = createLongLivedTokenAuth(hassUrl, token);
const connection = await createConnection({ auth });
```

**Key Methods:**
- `connection.sendMessagePromise()` - Send commands to HA
- `connection.subscribeEvents()` - Listen for HA events
- `subscribeEntities()` - Subscribe to entity state changes


