"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useApp } from "./Providers";
import { PageLoader } from "./Shell";
export function AuthGuard({ children }) { const {supabase}=useApp(); const router=useRouter(); const [user,setUser]=useState(); useEffect(()=>{supabase.auth.getUser().then(({data})=>data.user?setUser(data.user):router.replace("/login"));},[router,supabase]); if(!user)return <PageLoader/>; return children; }
