"use client";

import { useState } from "react";
import Link from "next/link";
import { useStudyCart } from "@/components/study-cart-provider";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ThemeToggle } from "@/components/theme-toggle";
import { LofiPlayer } from "@/components/lofi-player";
import {
  ArrowLeft,
  BookOpen,
  FileText,
  X,
  Download,
  ZoomIn,
  ZoomOut,
  ChevronLeft,
  ChevronRight,
  Trash2,
  Maximize2,
  Gamepad2,
  X as XIcon,
} from "lucide-react";
import { Progress } from "@/components/ui/progress";
import { useProgress } from "@/components/progress-provider";
import { useProgressSync } from "@/components/use-progress-sync";
import { CompactLeaderboard } from "@/components/compact-leaderboard";
import { useMemo, useEffect } from "react";

export default function StudyPage() {
  const { items, removeItem, clearCart } = useStudyCart();
  const { isFileComplete, isUnitComplete, toggleFileComplete } = useProgress();
  const [activeIndex, setActiveIndex] = useState(0);
  const [scale, setScale] = useState(100);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [currentUserId, setCurrentUserId] = useState<string | null>(null);
  
  // Brainrot mode only available with PRO=true in .env
  const isPro = process.env.NEXT_PUBLIC_PRO === "true";
  const [showSubwaySurfer, setShowSubwaySurfer] = useState(isPro);

  // Get the current course ID from active item
  const currentCourseId = items[activeIndex]?.courseId || "global";
  
  // WebSocket sync and leaderboard - must be called before any early returns
  // Full progress sync happens automatically on connect, reconnect, and periodically
  const { leaderboard, username, isConnected, requestLeaderboardUpdate } = useProgressSync(currentCourseId);
  
  // Get current user ID
  useEffect(() => {
    if (typeof window !== "undefined") {
      const userId = localStorage.getItem("progress-user-id");
      setCurrentUserId(userId);
    }
  }, []);

  const activeItem = items[activeIndex];

  const handleZoomIn = () => setScale((prev) => Math.min(prev + 25, 200));
  const handleZoomOut = () => setScale((prev) => Math.max(prev - 25, 50));

  const goToPrev = () => setActiveIndex((prev) => Math.max(0, prev - 1));
  const goToNext = () => {
    // Mark current PDF as complete before moving to next
    if (activeItem?.courseId && activeItem?.fileKey) {
      const isComplete = isFileComplete(activeItem.courseId, activeItem.fileKey);
      if (!isComplete) {
        toggleFileComplete(activeItem.courseId, activeItem.fileKey);
      }
    }
    // Request fresh leaderboard data from server
    requestLeaderboardUpdate();
    setActiveIndex((prev) => Math.min(items.length - 1, prev + 1));
  };

  // Memoize grouped items - MUST be before early return
  const groupedItems = useMemo(() => {
    const groups: Record<number, typeof items> = {} as any;
    items.forEach((it) => {
      groups[it.unitNumber] = groups[it.unitNumber] || [];
      groups[it.unitNumber].push(it);
    });
    const sortedUnits = Object.keys(groups)
      .map((k) => Number(k))
      .sort((a, b) => a - b);

    let globalIndex = 0;
    return sortedUnits.map((unitNum) => {
      const group = groups[unitNum];
      const unitComplete = group[0]?.courseId ? isUnitComplete(group[0].courseId!, unitNum) : false;
      const itemsWithIndex = group.map((item) => {
        const index = globalIndex++;
        const isActive = index === activeIndex;
        const isComplete =
          item.courseId && item.fileKey
            ? isFileComplete(item.courseId, item.fileKey)
            : false;
        return { item, index, isActive, isComplete };
      });
      return { unitNum, group, unitComplete, items: itemsWithIndex };
    });
  }, [items, activeIndex, isFileComplete, isUnitComplete]);

  // Early return AFTER all hooks are called
  if (items.length === 0) {
    return (
      <div className="min-h-screen bg-gradient-to-br from-slate-50 via-blue-50/50 to-indigo-50 dark:from-[#0f1219] dark:via-[#111827] dark:to-[#0f172a] flex items-center justify-center">
        <div className="text-center">
          <BookOpen className="h-16 w-16 mx-auto text-slate-300 dark:text-slate-600 mb-4" />
          <h1 className="text-2xl font-bold text-slate-900 dark:text-white mb-2">
            No PDFs in Study Queue
          </h1>
          <p className="text-slate-500 dark:text-slate-400 mb-6">
            Add PDFs from a course to start studying
          </p>
          <Link href="/courses">
            <Button className="bg-indigo-600 hover:bg-indigo-700">
              <ArrowLeft className="h-4 w-4 mr-2" />
              Browse Courses
            </Button>
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="h-screen flex bg-slate-100 dark:bg-slate-950">
      {/* Sidebar */}
      <div
        className={`${
          sidebarCollapsed ? "w-0" : "w-72"
        } transition-all duration-300 flex flex-col border-r border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 overflow-hidden`}
      >
        {/* Sidebar Header */}
        <div className="p-4 border-b border-slate-200 dark:border-slate-800 shrink-0">
          <div className="flex items-center justify-between mb-4">
            <Link href="/courses">
              <Button variant="ghost" size="sm" className="gap-2">
                <ArrowLeft className="h-4 w-4" />
                Courses
              </Button>
            </Link>
            <ThemeToggle />
          </div>
          <div className="flex items-center justify-between">
            <h2 className="font-semibold text-slate-900 dark:text-white">Study Queue</h2>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs text-slate-500 hover:text-red-500"
              onClick={clearCart}
            >
              <Trash2 className="h-3 w-3 mr-1" />
              Clear
            </Button>
          </div>
        </div>

        {/* PDF List */}
        <ScrollArea className="flex-1">
          <div className="p-2 space-y-2">
            {groupedItems.map(({ unitNum, unitComplete, items: unitItems }) => (
              <div key={`unit-${unitNum}`}>
                <div className="px-2 py-1 text-xs text-slate-400 flex items-center justify-between">
                  <span>Unit {unitNum}</span>
                  {unitComplete && <Badge variant="secondary">Unit Complete</Badge>}
                </div>
                <div className="space-y-1">
                  {unitItems.map(({ item, index, isActive, isComplete }) => (
                    <div
                      key={item.id}
                      className={`flex items-center gap-2 p-3 rounded-lg cursor-pointer group transition-colors min-w-0 ${
                        isActive
                          ? "bg-indigo-50 dark:bg-indigo-950/50 border border-indigo-200 dark:border-indigo-800"
                          : "hover:bg-slate-100 dark:hover:bg-slate-800"
                      }`}
                      onClick={() => setActiveIndex(index)}
                    >
                      <div className="relative shrink-0">
                        <FileText className="h-5 w-5 text-red-500" />
                        <Badge
                          className="absolute -top-2 -left-2 h-4 w-4 p-0 flex items-center justify-center text-[10px]"
                          variant={isActive ? "default" : "secondary"}
                        >
                          {index + 1}
                        </Badge>
                      </div>
                      <div className="flex-1 min-w-0">
                        <p className={`text-sm font-medium truncate ${isComplete ? 'text-green-400' : ''}`}>
                          {item.title}
                        </p>
                        <p className="text-xs text-slate-500 truncate">Unit {item.unitNumber}</p>
                      </div>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
                        onClick={(e) => {
                          e.stopPropagation();
                          removeItem(item.id);
                          if (index <= activeIndex && activeIndex > 0) {
                            setActiveIndex(activeIndex - 1);
                          }
                        }}
                      >
                        <X className="h-3 w-3" />
                      </Button>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </ScrollArea>
      </div>

      {/* Toggle Sidebar Button */}
      <button
        className="absolute left-0 top-1/2 -translate-y-1/2 z-10 bg-white dark:bg-slate-800 border border-slate-200 dark:border-slate-700 rounded-r-lg p-1.5 shadow-md hover:bg-slate-50 dark:hover:bg-slate-700 transition-all"
        style={{ left: sidebarCollapsed ? 0 : "18rem" }}
        onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
      >
        {sidebarCollapsed ? (
          <ChevronRight className="h-4 w-4" />
        ) : (
          <ChevronLeft className="h-4 w-4" />
        )}
      </button>

      {/* Main Content */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Toolbar with integrated race tracker */}
        <div className="border-b border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900 shrink-0">
          <div className="h-14 px-4 flex items-center justify-between">
            <div className="flex items-center gap-3 min-w-0">
              <div className="flex items-center gap-1">
                <Button
                  variant="outline"
                  size="icon"
                  className="h-8 w-8"
                  onClick={goToPrev}
                  disabled={activeIndex === 0}
                >
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <span className="text-sm font-medium w-16 text-center">
                  {activeIndex + 1} / {items.length}
                </span>
                <Button
                  variant="outline"
                  size="icon"
                  className="h-8 w-8"
                  onClick={goToNext}
                  disabled={activeIndex === items.length - 1}
                >
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
              <div className="h-6 w-px bg-slate-200 dark:bg-slate-700" />
              <p className="text-sm font-medium truncate text-slate-700 dark:text-slate-300">
                {activeItem?.title}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <div className="flex items-center gap-1 bg-slate-100 dark:bg-slate-800 rounded-md px-2 py-1">
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={handleZoomOut}
                  disabled={scale <= 50}
                >
                  <ZoomOut className="h-4 w-4" />
                </Button>
                <span className="text-xs font-medium w-12 text-center">{scale}%</span>
                <Button
                  variant="ghost"
                  size="icon"
                  className="h-7 w-7"
                  onClick={handleZoomIn}
                  disabled={scale >= 200}
                >
                  <ZoomIn className="h-4 w-4" />
                </Button>
              </div>
              {activeItem && (
                <>
                  <a href={activeItem.url} target="_blank" rel="noopener noreferrer">
                    <Button variant="outline" size="icon" className="h-8 w-8">
                      <Maximize2 className="h-4 w-4" />
                    </Button>
                  </a>
                  <a href={activeItem.url} download>
                    <Button variant="outline" size="icon" className="h-8 w-8">
                      <Download className="h-4 w-4" />
                    </Button>
                  </a>
                </>
              )}
            </div>
          </div>
          
          {/* Integrated race tracker timeline */}
          {isConnected && leaderboard.length > 0 && (
            <CompactLeaderboard
              entries={leaderboard}
              currentUserId={currentUserId}
              username={username}
            />
          )}
        </div>

        {/* Queue progress */}
        <div className="px-4 pt-2 pb-3 border-b border-slate-100 dark:border-slate-800 bg-white dark:bg-slate-900">
          <div className="flex items-center justify-between mb-2">
            <span className="text-xs text-slate-500">Queue Progress</span>
            <span className="text-xs text-slate-500">{activeIndex + 1}/{items.length}</span>
          </div>
          <Progress value={Math.round(((activeIndex + 1) / Math.max(1, items.length)) * 100)} className="h-2" />
        </div>

        {/* PDF Viewer */}
        <div className="flex-1 overflow-auto bg-slate-200 dark:bg-slate-950">
          {activeItem && (
            <iframe
              key={activeItem.id}
              src={`${activeItem.url}#toolbar=0&navpanes=0&view=FitH`}
              className="w-full h-full border-0"
              style={{
                transform: `scale(${scale / 100})`,
                transformOrigin: "top left",
                width: `${100 / (scale / 100)}%`,
                height: `${100 / (scale / 100)}%`,
              }}
              title={activeItem.title}
            />
          )}
        </div>
      </div>

      {/* Subway Surfer Video Panel - 9:16 aspect ratio (PRO feature) */}
      {isPro && showSubwaySurfer ? (
        <div className="h-full flex flex-col items-center justify-center bg-black border-l border-slate-800 p-2">
          {/* Close button */}
          <Button
            variant="ghost"
            size="icon"
            className="absolute top-2 right-2 z-10 h-6 w-6 text-slate-400 hover:text-white hover:bg-white/10 rounded-full"
            onClick={() => setShowSubwaySurfer(false)}
          >
            <XIcon className="h-3.5 w-3.5" />
          </Button>
          
          {/* Video Container with 9:16 aspect ratio */}
          <div className="relative h-full" style={{ aspectRatio: '9/16' }}>
            <iframe
              src="https://www.youtube.com/embed/zZ7AimPACzc?autoplay=1&mute=1&loop=1&playlist=zZ7AimPACzc&controls=0&modestbranding=1"
              className="absolute inset-0 w-full h-full rounded-lg"
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
              allowFullScreen
              title="Subway Surfer"
            />
          </div>
        </div>
      ) : isPro && !showSubwaySurfer ? (
        /* Toggle button when hidden (only if PRO) */
        <button
          onClick={() => setShowSubwaySurfer(true)}
          className="absolute right-4 top-20 z-10 bg-green-600 hover:bg-green-700 text-white rounded-full p-2 shadow-lg"
          title="Show Subway Surfer"
        >
          <Gamepad2 className="h-4 w-4" />
        </button>
      ) : null}

      {/* Lofi Music Player */}
      <LofiPlayer />
    </div>
  );
}
