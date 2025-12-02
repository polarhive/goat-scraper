"use client";

import { createContext, useContext, useEffect, useState, ReactNode } from "react";

interface ProgressData {
  [courseId: string]: {
    [fileKey: string]: boolean;
  };
}

interface ProgressContextType {
  progress: ProgressData;
  toggleFileComplete: (courseId: string, fileKey: string) => void;
  isFileComplete: (courseId: string, fileKey: string) => boolean;
  toggleUnitComplete: (courseId: string, fileKeys: string[], markComplete: boolean) => void;
  getUnitProgress: (courseId: string, unitNumber: number, totalFiles: number, fileKeys: string[]) => {
    completed: number;
    total: number;
    percentage: number;
  };
  isUnitComplete: (courseId: string, unitNumber: number) => boolean;
  getCourseProgress: (courseId: string, totalFiles: number, allFileKeys: string[]) => {
    completed: number;
    total: number;
    percentage: number;
  };
}

const ProgressContext = createContext<ProgressContextType | undefined>(undefined);

const STORAGE_KEY = "course-progress";

export function ProgressProvider({ children }: { children: ReactNode }) {
  const [progress, setProgress] = useState<ProgressData>({});
  const [isLoaded, setIsLoaded] = useState(false);

  // Load progress from localStorage on mount
  useEffect(() => {
    try {
      const stored = localStorage.getItem(STORAGE_KEY);
      if (stored) {
        setProgress(JSON.parse(stored));
      }
    } catch (e) {
      console.error("Failed to load progress:", e);
    }
    setIsLoaded(true);
  }, []);

  // Save progress to localStorage on change
  useEffect(() => {
    if (isLoaded) {
      try {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(progress));
      } catch (e) {
        console.error("Failed to save progress:", e);
      }
    }
  }, [progress, isLoaded]);

  const toggleFileComplete = (courseId: string, fileKey: string) => {
    setProgress((prev) => {
      const courseProgress = prev[courseId] || {};
      const isComplete = !courseProgress[fileKey];
      return {
        ...prev,
        [courseId]: {
          ...courseProgress,
          [fileKey]: isComplete,
        },
      };
    });
  };

  const isFileComplete = (courseId: string, fileKey: string): boolean => {
    return progress[courseId]?.[fileKey] ?? false;
  };

  const toggleUnitComplete = (courseId: string, fileKeys: string[], markComplete: boolean) => {
    setProgress((prev) => {
      const courseProgress = prev[courseId] || {};
      const updatedProgress = { ...courseProgress };
      fileKeys.forEach((key) => {
        updatedProgress[key] = markComplete;
      });
      return {
        ...prev,
        [courseId]: updatedProgress,
      };
    });
  };

  const isUnitComplete = (courseId: string, unitNumber: number): boolean => {
    const courseProgress = progress[courseId] || {};
    const keys = Object.keys(courseProgress).filter((k) => k.startsWith(`${unitNumber}-`));
    if (keys.length === 0) return false;
    return keys.every((k) => courseProgress[k]);
  };

  const getUnitProgress = (
    courseId: string,
    unitNumber: number,
    totalFiles: number,
    fileKeys: string[]
  ) => {
    const courseProgress = progress[courseId] || {};
    const completed = fileKeys.filter((key) => courseProgress[key]).length;
    return {
      completed,
      total: totalFiles,
      percentage: totalFiles > 0 ? Math.round((completed / totalFiles) * 100) : 0,
    };
  };

  const getCourseProgress = (
    courseId: string,
    totalFiles: number,
    allFileKeys: string[]
  ) => {
    const courseProgress = progress[courseId] || {};
    const completed = allFileKeys.filter((key) => courseProgress[key]).length;
    return {
      completed,
      total: totalFiles,
      percentage: totalFiles > 0 ? Math.round((completed / totalFiles) * 100) : 0,
    };
  };

  return (
    <ProgressContext.Provider
      value={{
        progress,
        toggleFileComplete,
        isFileComplete,
        toggleUnitComplete,
        getUnitProgress,
        isUnitComplete,
        getCourseProgress,
      }}
    >
      {children}
    </ProgressContext.Provider>
  );
}

export function useProgress() {
  const context = useContext(ProgressContext);
  if (!context) {
    throw new Error("useProgress must be used within a ProgressProvider");
  }
  return context;
}
