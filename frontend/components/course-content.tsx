"use client";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion";
import React, { useEffect, useRef, useState } from "react";
import { useProgress } from "@/components/progress-provider";
import { useStudyCart } from "@/components/study-cart-provider";
import { CourseSummary, ClassInfo } from "@/types/course";
import {
  FileText,
  Download,
  FileIcon,
  FileSpreadsheet,
  Presentation,
  CheckCircle2,
  Circle,
  Layers,
  File,
  Plus,
  Check,
  BookOpen,
} from "lucide-react";

interface CourseContentProps {
  summary: CourseSummary;
  basePath: string;
  courseId: string;
}

function getFileIcon(filename: string) {
  const ext = filename.toLowerCase().split(".").pop();
  switch (ext) {
    case "pdf":
      return <FileText className="h-4 w-4 text-red-500" />;
    case "pptx":
    case "ppt":
      return <Presentation className="h-4 w-4 text-orange-500" />;
    case "xlsx":
    case "xls":
      return <FileSpreadsheet className="h-4 w-4 text-green-500" />;
    case "docx":
    case "doc":
      return <FileIcon className="h-4 w-4 text-blue-500" />;
    default:
      return <File className="h-4 w-4 text-slate-500" />;
  }
}

function isPDF(filename: string): boolean {
  return filename.toLowerCase().endsWith(".pdf");
}

export function CourseContent({ summary, basePath, courseId }: CourseContentProps) {
  const { toggleFileComplete, isFileComplete, toggleUnitComplete, getUnitProgress, getCourseProgress } = useProgress();
  const { addItem, removeItem, isInCart } = useStudyCart();
  const { progress } = useProgress();
  const fileRowRefs = useRef<Record<string, HTMLDivElement | null>>({});
  const [openUnits, setOpenUnits] = useState<string[]>(["unit-1"]);

  // Generate all file keys for progress tracking
  const allFileKeys: string[] = [];
  summary.units.forEach((unit) => {
    unit.classes.forEach((cls) => {
      const primaryFilename = (cls as any).filename ?? (cls as any).files?.[0]?.filename ?? null;
      if (primaryFilename && cls.status === "success") {
        allFileKeys.push(`${unit.unit_number}-${cls.class_id}`);
      }
    });
  });

  const courseProgress = getCourseProgress(courseId, allFileKeys.length, allFileKeys);

  // On mount or when progress changes, scroll to last completed file and open its unit
  useEffect(() => {
    // find last completed key in traversal order
    let lastCompletedKey: string | null = null;
    summary.units.forEach((unit) => {
      unit.classes.forEach((cls) => {
        const primaryFilename = (cls as any).filename ?? (cls as any).files?.[0]?.filename ?? null;
        const key = `${unit.unit_number}-${cls.class_id}`;
        if (primaryFilename && progress[courseId]?.[key]) {
          lastCompletedKey = key;
        }
      });
    });

    // If found, open the unit and scroll into view
    if (lastCompletedKey) {
      const [unitNumberStr] = (lastCompletedKey as string).split("-");
      const unitVal = `unit-${unitNumberStr}`;
      setOpenUnits((prev) => (prev.includes(unitVal) ? prev : [...prev, unitVal]));
      // small delay to ensure DOM nodes exist
      setTimeout(() => {
        const el = fileRowRefs.current[lastCompletedKey!];
        if (el && typeof el.scrollIntoView === "function") {
          el.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      }, 150);
    }
  }, [progress, summary.units, courseId]);

  const handleAddToCart = (
    url: string,
    title: string,
    unitNumber: number,
    courseId?: string,
    fileKey?: string
  ) => {
    const id = url;
    if (isInCart(id)) {
      removeItem(id);
    } else {
      addItem({
        id,
        url,
        title,
        courseName: summary.course_name,
        unitNumber,
        courseId,
        fileKey,
      });
    }
  };

  return (
    <>
      {/* Overall Progress Card */}
      <Card className="mb-8">
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <BookOpen className="h-5 w-5" />
            Your Progress
          </CardTitle>
          <CardDescription>
            Track your learning progress through the course materials
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div className="flex items-center justify-between text-sm">
              <span className="text-slate-600 dark:text-slate-400">
                {courseProgress.completed} of {courseProgress.total} materials completed
              </span>
              <span className="font-semibold">
                {courseProgress.percentage}%
              </span>
            </div>
            <Progress value={courseProgress.percentage} className="h-3" />
            <div className="grid grid-cols-2 md:grid-cols-4 gap-4 pt-2">
                    {summary.units.map((unit) => {
                const unitFileKeys = unit.classes
                  .filter((cls) => cls.filename && cls.status === "success")
                  .map((cls) => `${unit.unit_number}-${cls.class_id}`);
                const unitProgress = getUnitProgress(
                  courseId,
                  unit.unit_number,
                  unitFileKeys.length,
                  unitFileKeys
                );
                return (
                  <div
                    key={unit.unit_number}
                    className="p-3 rounded-lg bg-slate-100/80 dark:bg-slate-800/40 border border-slate-200/50 dark:border-slate-700/50"
                  >
                    <div className="flex items-center gap-2 mb-2">
                      <Badge variant="outline" className="text-xs">
                        Unit {unit.unit_number}
                      </Badge>
                      {unitProgress.percentage === 100 && (
                        <CheckCircle2 className="h-4 w-4 text-green-500" />
                      )}
                    </div>
                    <Progress value={unitProgress.percentage} className="h-1.5" />
                    <p className="text-xs text-slate-500 mt-1">
                      {unitProgress.completed}/{unitProgress.total}
                    </p>
                  </div>
                );
              })}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Merged PDF Download */}
      <Card className="mb-8">
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <Download className="h-5 w-5" />
            Quick Downloads
          </CardTitle>
          <CardDescription>Download merged PDFs for each unit</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap gap-3">
            {summary.units.map((unit) => {
              if (!unit.merged_pdf) return null;
              const mergedPdf = `${basePath}/${unit.unit_directory}/${unit.merged_pdf}`;
              const inCart = isInCart(mergedPdf);
              return (
                <div key={unit.unit_number} className="flex gap-1">
                  <Button
                    variant={inCart ? "default" : "outline"}
                    className={`gap-2 ${inCart ? "bg-indigo-600 hover:bg-indigo-700" : ""}`}
                    onClick={() =>
                      handleAddToCart(
                        mergedPdf,
                        `Unit ${unit.unit_number} Merged`,
                        unit.unit_number,
                        courseId
                      )
                    }
                  >
                    {inCart ? <Check className="h-4 w-4" /> : <Plus className="h-4 w-4" />}
                    {inCart ? "Added" : "Add to Study"}
                  </Button>
                  <a href={mergedPdf} download className="inline-flex">
                    <Button variant="outline" className="gap-2">
                      <FileText className="h-4 w-4 text-red-500" />
                      Unit {unit.unit_number} Merged PDF
                    </Button>
                  </a>
                </div>
              );
            })}
          </div>
        </CardContent>
      </Card>

      {/* Units Accordion */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <Layers className="h-5 w-5" />
            Course Units
          </CardTitle>
          <CardDescription>Browse all course materials by unit</CardDescription>
        </CardHeader>
        <CardContent>
          <Accordion type="multiple" value={openUnits} onValueChange={(v) => setOpenUnits(Array.isArray(v) ? v : [v])} className="w-full">
            {summary.units.map((unit) => {
              const unitFileKeys = unit.classes
                .filter((cls) => {
                  const primaryFilename = (cls as any).filename ?? (cls as any).files?.[0]?.filename ?? null;
                  return primaryFilename && cls.status === "success";
                })
                .map((cls) => `${unit.unit_number}-${cls.class_id}`);
              const unitProgress = getUnitProgress(
                courseId,
                unit.unit_number,
                unitFileKeys.length,
                unitFileKeys
              );

              return (
                <AccordionItem key={unit.unit_number} value={`unit-${unit.unit_number}`}>
                  <AccordionTrigger className="hover:no-underline">
                    <div className="flex items-center justify-between w-full pr-4">
                      <div className="flex items-center gap-3">
                        <Badge variant="secondary">Unit {unit.unit_number}</Badge>
                        <span className="font-medium">
                          {unit.unit_name || `Unit ${unit.unit_number}`}
                        </span>
                        {unitProgress.percentage === 100 && (
                          <CheckCircle2 className="h-4 w-4 text-green-500" />
                        )}
                      </div>
                      <div className="flex items-center gap-3 text-sm text-slate-500">
                        <div className="hidden sm:flex items-center gap-2">
                          <Progress value={unitProgress.percentage} className="w-20 h-1.5" />
                          <span className="text-xs w-8">{unitProgress.percentage}%</span>
                        </div>
                        <span>{unit.total_files} files</span>
                        {unit.failed_files > 0 && (
                          <Badge variant="destructive" className="text-xs">
                            {unit.failed_files} failed
                          </Badge>
                        )}
                      </div>
                    </div>
                  </AccordionTrigger>
                  <AccordionContent>
                    {/* Unit-level toggle */}
                    <div className="flex items-center justify-between py-2 mb-2 border-b border-slate-100 dark:border-slate-800">
                      <span className="text-sm text-slate-500">
                        {unitProgress.completed}/{unitProgress.total} completed
                      </span>
                      <div className="flex items-center gap-2">
                        {/* Add whole unit to study (merged PDF if available, otherwise enqueue files) */}
                        <Button
                          variant="outline"
                          size="sm"
                          className="gap-2"
                          onClick={() => {
                            // Always add individual slides/files from the unit (not the merged PDF)
                            unit.classes.forEach((cls) => {
                              const primaryFilename = (cls as any).filename ?? (cls as any).files?.[0]?.filename ?? null;
                              if (primaryFilename && cls.status === "success") {
                                const filePath = `${basePath}/${unit.unit_directory}/${primaryFilename}`;
                                const fileKey = `${unit.unit_number}-${cls.class_id}`;
                                handleAddToCart(filePath, cls.class_name, unit.unit_number, courseId, fileKey);
                              }
                            });
                          }}
                        >
                          <FileText className="h-4 w-4" />
                          Add Unit to Study
                        </Button>

                        <Button
                          variant={unitProgress.percentage === 100 ? "secondary" : "outline"}
                          size="sm"
                          className="gap-2"
                          onClick={() => {
                            const markComplete = unitProgress.percentage < 100;
                            toggleUnitComplete(courseId, unitFileKeys, markComplete);
                          }}
                        >
                          {unitProgress.percentage === 100 ? (
                            <>
                              <CheckCircle2 className="h-4 w-4 text-green-500" />
                              Unit Complete
                            </>
                          ) : (
                            <>
                              <Circle className="h-4 w-4" />
                              Mark Unit Complete
                            </>
                          )}
                        </Button>
                      </div>
                    </div>
                    <div className="space-y-2 pt-2">
                      {unit.classes.length > 0 ? (
                        unit.classes.map((file: ClassInfo, idx: number) => {
                          const fileKey = `${unit.unit_number}-${file.class_id}`;
                          const isComplete = isFileComplete(courseId, fileKey);
                          const primaryFilename = (file as any).filename ?? (file as any).files?.[0]?.filename ?? null;
                          const filePath = primaryFilename
                            ? `${basePath}/${unit.unit_directory}/${primaryFilename}`
                            : null;
                          const canPreview = primaryFilename && isPDF(primaryFilename);

                          return (
                            <div
                                          ref={(el) => { fileRowRefs.current[fileKey] = el; }}
                              role="button"
                              tabIndex={0}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" || e.key === " ") {
                                  toggleFileComplete(courseId, fileKey);
                                }
                              }}
                              onClick={() => toggleFileComplete(courseId, fileKey)}
                              key={idx}
                              className={`flex items-center justify-between p-3 rounded-lg transition-all duration-200 ${
                                isComplete
                                  ? "bg-green-50 dark:bg-green-500/10 border border-green-200 dark:border-green-500/30"
                                  : "bg-slate-100/80 dark:bg-slate-800/40 hover:bg-slate-200/80 dark:hover:bg-slate-700/50 border border-transparent hover:border-slate-300 dark:hover:border-slate-600"
                              }`}
                            >
                              <div className="flex items-center gap-3 min-w-0">
                                {primaryFilename && file.status === "success" && (
                                  <button
                                    onClick={(e) => {
                                      e.stopPropagation();
                                      toggleFileComplete(courseId, fileKey);
                                    }}
                                    className="flex-shrink-0"
                                  >
                                    {isComplete ? (
                                      <CheckCircle2 className="h-5 w-5 text-green-500" />
                                    ) : (
                                      <Circle className="h-5 w-5 text-slate-400 dark:text-slate-500 hover:text-slate-500 dark:hover:text-slate-400" />
                                    )}
                                  </button>
                                )}
                                {primaryFilename && getFileIcon(primaryFilename)}
                                <div className="min-w-0">
                                  <p
                                    className={`font-medium text-sm truncate ${
                                      isComplete ? "text-green-700 dark:text-green-400" : ""
                                    }`}
                                  >
                                    {primaryFilename || file.class_name}
                                  </p>
                                  <p className="text-xs text-slate-500">{file.class_name}</p>
                                </div>
                              </div>
                              {primaryFilename && file.status === "success" ? (
                                <div className="flex items-center gap-2">
                                  {canPreview && (() => {
                                    const inCart = isInCart(filePath!);
                                    return (
                                      <Button
                                        size="sm"
                                        variant={inCart ? "secondary" : "ghost"}
                                        className="gap-2"
                                        onClick={(e) => {
                                          e.stopPropagation();
                                          handleAddToCart(
                                            filePath!,
                                            file.class_name,
                                            unit.unit_number,
                                            courseId,
                                            fileKey
                                          );
                                        }}
                                      >
                                        {inCart ? <Check className="h-4 w-4" /> : <Plus className="h-4 w-4" />}
                                        {inCart ? "Added" : "Study"}
                                      </Button>
                                    );
                                  })()}
                                  <a
                                    href={filePath!}
                                    download
                                    onClick={(e) => {
                                      // allow download but prevent toggling row
                                      e.stopPropagation();
                                    }}
                                  >
                                    <Button size="sm" variant="ghost" className="gap-2">
                                      <Download className="h-4 w-4" />
                                      Download
                                    </Button>
                                  </a>
                                </div>
                              ) : (
                                <Badge variant="destructive">Failed</Badge>
                              )}
                            </div>
                          );
                        })
                      ) : (
                        <p className="text-sm text-slate-500 text-center py-4">
                          No files in this unit
                        </p>
                      )}
                    </div>
                  </AccordionContent>
                </AccordionItem>
              );
            })}
          </Accordion>
        </CardContent>
      </Card>

    </>
  );
}
