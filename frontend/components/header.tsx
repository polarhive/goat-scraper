"use client";

import Link from "next/link";
import { ThemeToggle } from "@/components/theme-toggle";
import { CommandPalette } from "@/components/command-palette";
import { StudyCartButton } from "@/components/study-cart-button";
import { BookOpen } from "lucide-react";

interface SearchItem {
  type: "course" | "unit" | "file";
  title: string;
  subtitle?: string;
  href: string;
  download?: boolean;
}

interface HeaderProps {
  searchItems?: SearchItem[];
  showBackButton?: boolean;
  backHref?: string;
}

export function Header({ searchItems = [] }: HeaderProps) {
  return (
    <header className="border-b border-slate-200 dark:border-slate-700/60 bg-white/80 dark:bg-slate-900/80 backdrop-blur-sm sticky top-0 z-50">
      <div className="container mx-auto px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-6">
            <Link href="/" className="flex items-center gap-3 hover:opacity-80 transition-opacity">
              <div className="p-2 bg-indigo-600 dark:bg-indigo-500 rounded-lg shadow-md shadow-indigo-500/20">
                <BookOpen className="h-6 w-6 text-white" />
              </div>
              <div>
                <h1 className="text-xl font-bold text-slate-900 dark:text-white">🐐</h1>
                <p className="text-sm text-slate-500 dark:text-slate-400">Course Materials Portal</p>
              </div>
            </Link>
          </div>
          <div className="flex items-center gap-2">
            <div className="flex items-center gap-2">
              <CommandPalette items={searchItems} />
              <StudyCartButton />
            </div>
            <div className="ml-4">
              <ThemeToggle />
            </div>
          </div>
        </div>
      </div>
    </header>
  );
}
