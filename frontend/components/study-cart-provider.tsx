"use client";

import { createContext, useContext, useState, useEffect, ReactNode } from "react";

export interface StudyItem {
  id: string;
  url: string;
  title: string;
  courseName: string;
  unitNumber: number;
  // optional linkage to progress tracking
  courseId?: string;
  fileKey?: string; // e.g. "1-c8988bc7-..."
}

interface StudyCartContextType {
  items: StudyItem[];
  addItem: (item: StudyItem) => void;
  removeItem: (id: string) => void;
  clearCart: () => void;
  isInCart: (id: string) => boolean;
  itemCount: number;
}

const StudyCartContext = createContext<StudyCartContextType | undefined>(undefined);

const STORAGE_KEY = "study-cart";

export function StudyCartProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<StudyItem[]>([]);
  const [isLoaded, setIsLoaded] = useState(false);

  // Load from localStorage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        setItems(JSON.parse(stored));
      }
    } catch (e) {
      console.error("Failed to load study cart:", e);
    }
    setIsLoaded(true);
  }, []);

  // Save to localStorage on change
  useEffect(() => {
    if (isLoaded) {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
      } catch (e) {
        console.error("Failed to save study cart:", e);
      }
    }
  }, [items, isLoaded]);

  const addItem = (item: StudyItem) => {
    setItems((prev) => {
      if (prev.find((i) => i.id === item.id)) {
        return prev;
      }
      return [...prev, item];
    });
  };

  const removeItem = (id: string) => {
    setItems((prev) => prev.filter((i) => i.id !== id));
  };

  const clearCart = () => {
    setItems([]);
  };

  const isInCart = (id: string): boolean => {
    return items.some((i) => i.id === id);
  };

  return (
    <StudyCartContext.Provider
      value={{
        items,
        addItem,
        removeItem,
        clearCart,
        isInCart,
        itemCount: items.length,
      }}
    >
      {children}
    </StudyCartContext.Provider>
  );
}

export function useStudyCart() {
  const context = useContext(StudyCartContext);
  if (!context) {
    throw new Error("useStudyCart must be used within a StudyCartProvider");
  }
  return context;
}
