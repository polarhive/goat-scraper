"use client";

import { useState } from "react";
import Link from "next/link";
import { useStudyCart } from "@/components/study-cart-provider";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ShoppingCart, X, BookOpen, Trash2, FileText } from "lucide-react";

export function StudyCartButton() {
  const { items, removeItem, clearCart, itemCount } = useStudyCart();
  const [open, setOpen] = useState(false);

  return (
    <DropdownMenu open={open} onOpenChange={setOpen}>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="icon" className="relative">
          <ShoppingCart className="h-4 w-4" />
          {itemCount > 0 && (
            <Badge 
              className="absolute -top-2 -right-2 h-5 w-5 flex items-center justify-center p-0 text-xs bg-indigo-600"
            >
              {itemCount}
            </Badge>
          )}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-80">
        <DropdownMenuLabel className="flex items-center justify-between">
          <span>Study Queue</span>
          {itemCount > 0 && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 text-xs text-slate-500 hover:text-red-500"
              onClick={(e) => {
                e.preventDefault();
                clearCart();
              }}
            >
              <Trash2 className="h-3 w-3 mr-1" />
              Clear
            </Button>
          )}
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {items.length === 0 ? (
          <div className="py-8 text-center">
            <ShoppingCart className="h-8 w-8 mx-auto text-slate-300 dark:text-slate-600 mb-2" />
            <p className="text-sm text-slate-500">No PDFs added yet</p>
            <p className="text-xs text-slate-400 mt-1">Click + on PDFs to add them</p>
          </div>
        ) : (
          <>
            <ScrollArea className="h-[280px]">
              <div className="space-y-1 p-1">
                {items.map((item) => (
                  <div
                      key={item.id}
                      className="flex items-center gap-2 p-2 rounded-md hover:bg-slate-100 dark:hover:bg-slate-800 group min-w-0"
                      style={{ overflow: 'hidden' }}
                    >
                    <FileText className="h-4 w-4 text-red-500 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium truncate">{item.title}</p>
                      <p className="text-xs text-slate-500 truncate">
                        Unit {item.unitNumber} • {item.courseName}
                      </p>
                    </div>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="h-6 w-6 opacity-0 group-hover:opacity-100 transition-opacity"
                      onClick={(e) => {
                        e.preventDefault();
                        removeItem(item.id);
                      }}
                    >
                      <X className="h-3 w-3" />
                    </Button>
                  </div>
                ))}
              </div>
            </ScrollArea>
            <DropdownMenuSeparator />
            <div className="p-2">
              <Link href="/study" onClick={() => setOpen(false)}>
                <Button className="w-full bg-indigo-600 hover:bg-indigo-700">
                  <BookOpen className="h-4 w-4 mr-2" />
                  Open Study View ({itemCount})
                </Button>
              </Link>
            </div>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
