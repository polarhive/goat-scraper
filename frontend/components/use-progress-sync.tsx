"use client";

import { useEffect, useRef, useState, useCallback } from "react";

interface LeaderboardEntry {
  userId: string;
  username: string;
  completed: number;
  total: number;
  percentage: number;
  lastUpdate: string;
}

interface ProgressUpdate {
  courseId: string;
  fileKey: string;
  isComplete: boolean;
}

interface UseProgressSyncReturn {
  leaderboard: LeaderboardEntry[];
  username: string;
  isConnected: boolean;
  currentUserRank: number | null;
  updateUsername: (newUsername: string) => void;
  requestLeaderboardUpdate: () => void;
  syncStudyItems: (fileKeys: string[]) => void;
  syncFullProgress: (progress: any, studyItems: any) => void;
}

// Generate a random anonymous username
const generateUsername = () => {
  const adjectives = [
    "Swift", "Clever", "Bright", "Keen", "Sharp", "Quick", "Bold", "Calm", 
    "Wise", "Agile", "Smart", "Brave", "Cool", "Epic", "Fast", "Great"
  ];
  const nouns = [
    "Panda", "Tiger", "Eagle", "Wolf", "Fox", "Bear", "Lion", "Hawk",
    "Owl", "Falcon", "Lynx", "Jaguar", "Panther", "Dragon", "Phoenix", "Raven"
  ];
  
  const adj = adjectives[Math.floor(Math.random() * adjectives.length)];
  const noun = nouns[Math.floor(Math.random() * nouns.length)];
  const num = Math.floor(Math.random() * 999) + 1;
  
  return `${adj}${noun}${num}`;
};

// Get or create a persistent user ID
const getUserId = () => {
  if (typeof window === "undefined") return null;
  
  let userId = localStorage.getItem("progress-user-id");
  if (!userId) {
    userId = `user_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    localStorage.setItem("progress-user-id", userId);
  }
  return userId;
};

// Get or create a persistent username
const getUsername = () => {
  if (typeof window === "undefined") return "";
  
  let username = localStorage.getItem("progress-username");
  if (!username) {
    username = generateUsername();
    localStorage.setItem("progress-username", username);
  }
  return username;
};

export function useProgressSync(courseId: string): UseProgressSyncReturn {
  const [leaderboard, setLeaderboard] = useState<LeaderboardEntry[]>([]);
  const [isConnected, setIsConnected] = useState(false);
  const [username, setUsername] = useState("");
  const [userId, setUserId] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | undefined>(undefined);
  const pendingUpdatesRef = useRef<ProgressUpdate[]>([]);

  // Initialize userId and username
  useEffect(() => {
    const id = getUserId();
    const name = getUsername();
    setUserId(id);
    setUsername(name);
  }, []);

  // Get WebSocket URL from environment or default
  const getWebSocketUrl = () => {
    if (typeof window === "undefined") return "ws://localhost:8000";
    return process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";
  };

  // Update username and notify server
  const updateUsername = useCallback((newUsername: string) => {
    if (!newUsername.trim()) return;
    
    setUsername(newUsername);
    localStorage.setItem("progress-username", newUsername);
    
    // Notify server of username change
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: "set_username",
        username: newUsername,
      }));
    }
  }, []);

  // Connect to WebSocket
  const connect = useCallback(() => {
    if (!userId || typeof window === "undefined") return;

    try {
      const wsUrl = getWebSocketUrl();
      const ws = new WebSocket(`${wsUrl}/ws/${userId}`);
      
      ws.onopen = () => {
        console.log("Connected to progress tracking server");
        setIsConnected(true);
        
        // Sync full progress state from localStorage (client is source of truth)
        try {
          const storedProgress = localStorage.getItem("course-progress");
          const storedCart = localStorage.getItem("study-cart");
          
          if (storedProgress) {
            const progress = JSON.parse(storedProgress);
            
            // Build studyItems map from cart
            const studyItems: Record<string, string[]> = {};
            if (storedCart) {
              const cartItems = JSON.parse(storedCart);
              cartItems.forEach((item: any) => {
                if (item.courseId && item.fileKey) {
                  if (!studyItems[item.courseId]) {
                    studyItems[item.courseId] = [];
                  }
                  studyItems[item.courseId].push(item.fileKey);
                }
              });
            }
            
            // Send bulk sync
            ws.send(JSON.stringify({
              type: "sync_full_progress",
              progress,
              studyItems,
              username,
            }));
            console.log("Synced full progress state to server");
          }
        } catch (error) {
          console.error("Failed to sync progress:", error);
        }
        
        // Clear any pending individual updates (no longer needed)
        pendingUpdatesRef.current = [];
        
        // Request initial leaderboard
        ws.send(JSON.stringify({
          type: "request_leaderboard",
          courseId,
        }));
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          
          if (data.type === "leaderboard_update" && data.courseId === courseId) {
            setLeaderboard(data.leaderboard);
          } else if (data.type === "connected") {
            console.log("Server confirmed connection");
          }
        } catch (error) {
          console.error("Error parsing WebSocket message:", error);
        }
      };

      ws.onerror = (error) => {
        console.error("WebSocket error:", error);
      };

      ws.onclose = () => {
        console.log("Disconnected from progress tracking server");
        setIsConnected(false);
        
        // Attempt to reconnect after 3 seconds
        reconnectTimeoutRef.current = setTimeout(() => {
          console.log("Attempting to reconnect...");
          connect();
        }, 3000);
      };

      wsRef.current = ws;
    } catch (error) {
      console.error("Failed to connect to WebSocket:", error);
      setIsConnected(false);
    }
  }, [userId, courseId, username]);

  // Connect on mount and cleanup on unmount
  useEffect(() => {
    if (userId) {
      connect();
    }

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [userId, connect]);

  // Poll for leaderboard updates every 5 seconds
  useEffect(() => {
    if (!isConnected || !courseId) return;

    const pollInterval = setInterval(() => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({
          type: "request_leaderboard",
          courseId,
        }));
      }
    }, 5000);

    return () => clearInterval(pollInterval);
  }, [isConnected, courseId]);

  // Periodic full sync every 30 seconds when connected
  useEffect(() => {
    if (!isConnected) return;

    const syncInterval = setInterval(() => {
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        try {
          const storedProgress = localStorage.getItem("course-progress");
          const storedCart = localStorage.getItem("study-cart");
          
          if (storedProgress) {
            const progress = JSON.parse(storedProgress);
            
            // Build studyItems map from cart
            const studyItems: Record<string, string[]> = {};
            if (storedCart) {
              const cartItems = JSON.parse(storedCart);
              cartItems.forEach((item: any) => {
                if (item.courseId && item.fileKey) {
                  if (!studyItems[item.courseId]) {
                    studyItems[item.courseId] = [];
                  }
                  studyItems[item.courseId].push(item.fileKey);
                }
              });
            }
            
            wsRef.current!.send(JSON.stringify({
              type: "sync_full_progress",
              progress,
              studyItems,
              username,
            }));
            console.log("Periodic sync completed");
          }
        } catch (error) {
          console.error("Failed to sync progress:", error);
        }
      }
    }, 30000); // Every 30 seconds

    return () => clearInterval(syncInterval);
  }, [isConnected, username]);

  // Handle online/offline events
  useEffect(() => {
    if (typeof window === "undefined") return;

    const handleOnline = () => {
      console.log("Browser came online, reconnecting...");
      if (!isConnected) {
        connect();
      }
    };

    const handleOffline = () => {
      console.log("Browser went offline");
    };

    window.addEventListener("online", handleOnline);
    window.addEventListener("offline", handleOffline);

    return () => {
      window.removeEventListener("online", handleOnline);
      window.removeEventListener("offline", handleOffline);
    };
  }, [isConnected, connect]);

  // Listen for localStorage changes and trigger sync
  useEffect(() => {
    if (typeof window === "undefined" || !isConnected) return;

    const handleStorageChange = (e: StorageEvent) => {
      // Only sync if course-progress or study-cart changed
      if (e.key === "course-progress" || e.key === "study-cart") {
        // Debounce to avoid too many syncs
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          try {
            const storedProgress = localStorage.getItem("course-progress");
            const storedCart = localStorage.getItem("study-cart");
            
            if (storedProgress) {
              const progress = JSON.parse(storedProgress);
              
              const studyItems: Record<string, string[]> = {};
              if (storedCart) {
                const cartItems = JSON.parse(storedCart);
                cartItems.forEach((item: any) => {
                  if (item.courseId && item.fileKey) {
                    if (!studyItems[item.courseId]) {
                      studyItems[item.courseId] = [];
                    }
                    studyItems[item.courseId].push(item.fileKey);
                  }
                });
              }
              
              wsRef.current.send(JSON.stringify({
                type: "sync_full_progress",
                progress,
                studyItems,
                username,
              }));
              console.log("Synced after localStorage change");
            }
          } catch (error) {
            console.error("Failed to sync after storage change:", error);
          }
        }
      }
    };

    window.addEventListener("storage", handleStorageChange);

    return () => {
      window.removeEventListener("storage", handleStorageChange);
    };
  }, [isConnected, username]);

  // Send progress update to server
  const sendProgressUpdate = useCallback((fileKey: string, isComplete: boolean) => {
    const update: ProgressUpdate = {
      courseId,
      fileKey,
      isComplete,
    };

    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: "progress_update",
        ...update,
        username,
      }));
    } else {
      // Queue update if not connected
      pendingUpdatesRef.current.push(update);
    }
  }, [courseId, username]);

  // Expose sendProgressUpdate for external use
  useEffect(() => {
    if (typeof window !== "undefined") {
      (window as any).__sendProgressUpdate = sendProgressUpdate;
    }
  }, [sendProgressUpdate]);

  // Request immediate leaderboard update
  const requestLeaderboardUpdate = useCallback(() => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN && courseId) {
      wsRef.current.send(JSON.stringify({
        type: "request_leaderboard",
        courseId,
      }));
    }
  }, [courseId]);

  // Sync study items (files in study bucket) to server
  const syncStudyItems = useCallback((fileKeys: string[]) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN && courseId) {
      wsRef.current.send(JSON.stringify({
        type: "sync_study_items",
        courseId,
        fileKeys,
      }));
    }
  }, [courseId]);

  // Sync full progress state from localStorage to server (client is source of truth)
  const syncFullProgress = useCallback((progress: any, studyItems: any) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({
        type: "sync_full_progress",
        progress,
        studyItems,
        username,
      }));
    }
  }, [username]);

  // Calculate current user's rank
  const currentUserRank = userId 
    ? leaderboard.findIndex(entry => entry.userId === userId) + 1 
    : null;

  return {
    leaderboard,
    username,
    isConnected,
    currentUserRank: currentUserRank !== null && currentUserRank > 0 ? currentUserRank : null,
    updateUsername,
    requestLeaderboardUpdate,
    syncStudyItems,
    syncFullProgress,
  };
}
