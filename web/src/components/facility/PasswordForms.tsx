"use client";

import { ForgotPasswordForm, SetPasswordForm } from "@/components/account/PasswordForms";
import { facilityApi } from "@/lib/facility";

export function FacilityForgotPasswordForm() {
  return <ForgotPasswordForm send={facilityApi.forgotPassword} emailLabel="Your work email address" />;
}

export function FacilitySetPasswordForm() {
  return (
    <SetPasswordForm save={facilityApi.setPassword} home="/facility" forgotHref="/facility/forgot-password" />
  );
}
